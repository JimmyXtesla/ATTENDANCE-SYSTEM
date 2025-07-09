# app.py
import os
import uuid
import csv
import io
from datetime import datetime
from functools import wraps

from flask import (Flask, render_template, request, redirect, url_for,
                   flash, session, make_response)
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from dotenv import load_dotenv

# --- App Configuration ---
load_dotenv()
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'a-super-secret-key-for-dev')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///attendance.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# --- Database Setup ---
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# --- Admin Credentials (from .env file) ---
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'password')

# --- Database Models ---
class AccessLink(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<AccessLink {self.token} (Active: {self.is_active})>"

class Attendee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(50), nullable=False)
    group = db.Column(db.String(100), nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    access_token_used = db.Column(db.String(36), nullable=False)

    def __repr__(self):
        return f"<Attendee {self.name}>"

# --- Authentication Decorator ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

# --- User-Facing Routes ---
@app.route('/register/<string:token>', methods=['GET', 'POST'])
def register(token):
    link = AccessLink.query.filter_by(token=token, is_active=True).first()
    if not link:
        return render_template('invalid_link.html'), 404

    if request.method == 'POST':
        name = request.form.get('name')
        role = request.form.get('role')
        group = request.form.get('group')

        if not name or not role:
            flash('Name and Role are required fields.', 'danger')
            return redirect(url_for('register', token=token))

        new_attendee = Attendee(
            name=name,
            role=role,
            group=group,
            access_token_used=token
        )
        db.session.add(new_attendee)
        db.session.commit()
        return redirect(url_for('success'))

    # Hardcoded roles for the dropdown
    roles = ["Member", "Leader", "Guest", "First-timer", "Volunteer"]
    return render_template('register.html', token=token, roles=roles)

@app.route('/success')
def success():
    return render_template('success.html')

# --- Admin Routes ---
@app.route('/')
def home():
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if session.get('admin_logged_in'):
        return redirect(url_for('admin_dashboard'))
        
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            flash('Login successful!', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Invalid credentials. Please try again.', 'danger')
    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('admin_login'))

@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    # Sorting logic
    sort_by = request.args.get('sort_by', 'timestamp')
    order = request.args.get('order', 'desc')
    
    sortable_columns = {'name', 'role', 'group', 'timestamp'}
    if sort_by not in sortable_columns:
        sort_by = 'timestamp'

    query = Attendee.query
    if order == 'asc':
        query = query.order_by(getattr(Attendee, sort_by).asc())
    else:
        query = query.order_by(getattr(Attendee, sort_by).desc())

    attendees = query.all()
    links = AccessLink.query.order_by(AccessLink.created_at.desc()).all()
    active_link = AccessLink.query.filter_by(is_active=True).first()

    return render_template(
        'admin_dashboard.html', 
        attendees=attendees, 
        links=links, 
        active_link=active_link,
        sort_by=sort_by,
        order=order
    )

@app.route('/admin/generate-link', methods=['POST'])
@login_required
def generate_link():
    # Deactivate all existing links for better security
    AccessLink.query.update({AccessLink.is_active: False})
    
    # Create a new active link
    new_link = AccessLink()
    db.session.add(new_link)
    db.session.commit()
    flash(f'New access link generated and activated. Previous links have been deactivated.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/toggle-link/<int:link_id>', methods=['POST'])
@login_required
def toggle_link_status(link_id):
    link = AccessLink.query.get_or_404(link_id)
    # If we are activating this link, deactivate all others
    if not link.is_active:
        AccessLink.query.update({AccessLink.is_active: False})
        link.is_active = True
        flash(f'Link {link.token[:8]}... has been activated. All other links deactivated.', 'success')
    else:
        link.is_active = False
        flash(f'Link {link.token[:8]}... has been deactivated.', 'warning')

    db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/export-csv')
@login_required
def export_csv():
    attendees = Attendee.query.order_by(Attendee.timestamp.desc()).all()
    
    # Use io.StringIO to create a file in memory
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow(['ID', 'Name', 'Role', 'Group', 'Timestamp (UTC)', 'Access Token Used'])
    
    # Write data
    for attendee in attendees:
        writer.writerow([
            attendee.id,
            attendee.name,
            attendee.role,
            attendee.group,
            attendee.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            attendee.access_token_used
        ])
    
    output.seek(0)
    
    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=attendance_export.csv"
    response.headers["Content-type"] = "text/csv"
    
    return response

if __name__ == '__main__':
    app.run(debug=True)