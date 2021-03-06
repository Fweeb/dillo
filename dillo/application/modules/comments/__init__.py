import datetime
from flask import render_template
from flask import Blueprint
from flask import redirect
from flask import url_for
from flask import jsonify
from flask import abort
from flask import request

from flask.ext.security import login_required
from flask.ext.security import current_user

from application import app
from application import db

from application.modules.posts.model import Post
from application.modules.posts.model import Comment
from application.modules.posts.model import CommentRating
from application.modules.posts.model import UserCommentRating
from application.modules.posts.forms import CommentForm
from application.modules.notifications import notification_subscribe
from application.modules.notifications import notification_object_add
from application.modules.notifications import notification_get_subscriptions
from application.helpers import encode_id
from application.helpers import decode_id
from application.helpers import bleach_input
from application.helpers import delete_redis_cache_post

comments = Blueprint('comments', __name__)


@comments.route('/<int:post_id>/')
@login_required
def index(post_id):
    post = Post.query.get_or_404(post_id)
    comments = []
    for comment in post.comments_first_level:
        comments.append({
            'uuid' : comment.uuid,
            'user_id' : comment.user.id
            })
    #comments = [comment for comment in post.comments_first_level()]
    return jsonify(comments=comments)


@comments.route('/<int:post_id>/submit', methods=['POST'])
@login_required
def submit(post_id):
    form = CommentForm()
    post = Post.query.get_or_404(post_id)
    if form.validate_on_submit():
        parent_id = form.parent_id.data
        if not parent_id:
            parent_id = None
        else:
            parent_id = int(form.parent_id.data)
        comment = Comment(
            user_id=current_user.id,
            post_id=post.id,
            parent_id=parent_id,
            content = bleach_input(form.content.data))
        db.session.add(comment)
        db.session.commit()
        comment.uuid = encode_id(comment.id)
        comment_rating = CommentRating(
            comment_id=comment.id,
            positive=0,
            negative=0
            )
        db.session.add(comment_rating)
        db.session.commit()

        # Subscribe user to post (skips if already subscribed)
        notification_subscribe(current_user.id, 1, post.id)

        if parent_id:
            # If the comment is a reply notify the subscribed users
            notification_object_add(current_user.id, 'replied', 2, comment.id,
                2, parent_id)
            # Subscribe to comments to parent comment
            notification_subscribe(current_user.id, 2, parent_id)
        else:
            # Push notification to users subscribed to the post
            notification_object_add(current_user.id, 'commented', 2, comment.id,
                1, post.id)
            # Subscribe to comments to own comment
            notification_subscribe(current_user.id, 2, comment.id)

        # Clear cache for current user
        delete_redis_cache_post(post.uuid)
        # Clear cache for all subscribed users
        for subscription in notification_get_subscriptions(
            1, post.id, current_user.id):
            delete_redis_cache_post(post.uuid, subscription.user_id)

        # This is to prevent encoding error when jsonify prints out
        # a non ASCII name
        if comment.user.first_name and comment.user.last_name:
            display_name = u"{0} {1}".format(
                comment.user.first_name,
                comment.user.last_name)
        else:
            display_name = comment.user.username

        return jsonify(comment=dict(
            user_name=display_name,
            gravatar=comment.user.gravatar(),
            content=comment.content,
            comment_id=comment.id,
            parent_id=comment.parent_id,
            post_uuid=post.uuid,
            creation_date=comment.pretty_creation_date,
            post_url=url_for('posts.view',
                category=post.category.url,
                uuid=post.uuid,
                slug=post.slug)
            ))
    else:
        return abort(400)

    # return redirect(url_for('posts.view',
    #     category=post.category.url,
    #     uuid=post.uuid))


@comments.route('/<int:comment_id>/rate/<int:rating>')
@login_required
def rate(comment_id, rating):
    comment = Comment.query.get_or_404(comment_id)
    # Check if comment has been rated
    user_comment_rating = UserCommentRating.query\
        .filter_by(comment_id=comment.id, user_id=current_user.id).first()
    # If a rating exists, we update the the user comment record and the comment
    # record accordingly
    if user_comment_rating:
        if user_comment_rating.is_positive != rating:
            user_comment_rating.is_positive = rating
            if user_comment_rating.is_positive:
                comment.rating.positive += 1
                comment.rating.negative -= 1
                comment.user.karma.positive += 1
                comment.user.karma.negative -= 1
            else:
                comment.rating.negative += 1
                comment.rating.positive -= 1
                comment.user.karma.negative += 1
                comment.user.karma.positive -= 1
            db.session.commit()
        else:
            # Remove existing rate
            if user_comment_rating.is_positive:
                comment.rating.positive -= 1
                comment.user.karma.positive -= 1
            else:
                comment.rating.negative -= 1
                comment.user.karma.negative -= 1
            db.session.delete(user_comment_rating)
            db.session.commit()
            comment.user.update_karma()
            # Clear all the caches
            post = Post.query.get(comment.post_id)
            delete_redis_cache_post(post.uuid)
            return jsonify(rating=None,
                rating_delta=comment.rating_delta)
    else:
        user_comment_rating = UserCommentRating(
            user_id=current_user.id,
            comment_id=comment.id,
            is_positive=rating)
        if user_comment_rating.is_positive:
            comment.rating.positive += 1
            comment.user.karma.positive += 1
        else:
            comment.rating.negative += 1
            comment.user.karma.positive += 1
        db.session.add(user_comment_rating)
        db.session.commit()
        comment.user.update_karma()
        # Clear all the caches
        post = Post.query.get(comment.post_id)
        delete_redis_cache_post(post.uuid)
    return jsonify(rating=str(user_comment_rating.is_positive),
        rating_delta=comment.rating_delta)


@comments.route('/<int:comment_id>/flag')
@login_required
def flag(comment_id):
    comment = Comment.query.get_or_404(comment_id)
    # Get comment
    user_comment_rating = UserCommentRating.query\
        .filter_by(comment_id=comment.id, user_id=current_user.id).first()
    # Check if user rated the comment
    if user_comment_rating:
        # If the comment was previously flagged
        if user_comment_rating.is_flagged == True:
            user_comment_rating.is_flagged = 0
            comment.user.karma.negative += 5
        else:
            user_comment_rating.is_flagged = 1
            comment.user.karma.negative -= 5
    else:
        user_comment_rating = UserCommentRating(
            user_id=current_user.id,
            comment_id=comment.id,
            is_flagged=True)
        comment.user.karma.negative += 5
        db.session.add(user_comment_rating)

    # Commit changes so far
    db.session.commit()

    # Check if the comment has been flagged multiple times, currently
    # the value is hardcoded to 5
    flags = UserCommentRating.query\
        .filter_by(comment_id=comment.id, is_flagged=True)\
        .all()
    if len(flags) > 5:
        comment.status = 'flagged'
    comment.user.update_karma()
    # Clear all the caches
    post = Post.query.get(comment.post_id)
    delete_redis_cache_post(post.uuid)

    return jsonify(is_flagged=user_comment_rating.is_flagged)


@comments.route('/<int:comment_id>/edit', methods=['POST'])
@login_required
def edit(comment_id):
    comment = Comment.query.get_or_404(comment_id)
    if current_user.id == comment.user.id or (current_user.has_role('admin')):
        comment.content = bleach_input(request.form['content'])
        comment.status = 'edited'
        comment.edit_date = datetime.datetime.now()
        db.session.commit()

        delete_redis_cache_post(comment.post.uuid, all_users=True)
        return jsonify(status='edited')
    else:
        return abort(403)


@comments.route('/<int:comment_id>/delete', methods=['GET'])
@login_required
def delete(comment_id):
    comment = Comment.query.get_or_404(comment_id)
    if (current_user.id == comment.user.id) or (current_user.has_role('admin')):

        children = Comment.query.filter_by(parent_id=comment.id).all()

        for child in children:
            child.status = 'deleted'
            child.edit_date = datetime.datetime.now()
            db.session.commit()

        comment.status = 'deleted'
        comment.edit_date = datetime.datetime.now()

        db.session.commit()

        delete_redis_cache_post(comment.post.uuid, all_users=True)
        return jsonify(status='deleted')
    else:
        return abort(403)
