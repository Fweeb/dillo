from flask import render_template
from flask import redirect
from flask import url_for
from flask import request
from flask import flash
from flask import send_from_directory

from flask.ext.security import login_required

from application import app
from application.modules.pages import view


# Views
@app.route('/about')
def about():
    return view('about')

@app.route('/')
def index():
    return redirect(url_for('posts.index'))

@app.route('/faq')
def faq():
    return view('faq')

@app.route('/terms')
def terms():
    return view('terms')

@app.route('/robots.txt')
def static_from_root():
    return send_from_directory(app.static_folder, request.path[1:])