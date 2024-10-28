from flask import Flask, render_template, request, redirect, url_for, send_file, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import joinedload
from sqlalchemy.exc import IntegrityError
from xhtml2pdf import pisa
import qrcode
import resend
import os
import logging
import base64
from datetime import datetime, timezone
from dotenv import load_dotenv 

# Load environment variables
load_dotenv()

resend.api_key = os.getenv("RESEND_API_KEY")

# Configure Flask app and SQLAlchemy
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('SQLALCHEMY_DATABASE_URI')
app.secret_key = os.getenv('SECRET_KEY') 
db = SQLAlchemy(app)

# Configure logging
logging.basicConfig(filename='error.log', level=logging.ERROR)

# Models
class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    roll_number = db.Column(db.String(20), unique=True, nullable=False)
    class_name = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    qr_code_path = db.Column(db.String(100), nullable=True)

class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'))
    status = db.Column(db.String(10), nullable=False)

    student = db.relationship('Student', backref='attendances')
    __table_args__ = (db.UniqueConstraint('date', 'student_id', name='unique_attendance_constraint'),)

# Utility functions
def ensure_directory_exists(path):
    if not os.path.exists(path):
        os.makedirs(path)

def link_callback(uri, rel):
    if uri.startswith("static/"):
        return os.path.join(os.path.abspath("."), uri)
    return uri

def send_email_with_qr(email, name, qr_code_path):
    # Read the QR code image and encode it as base64
    with open(qr_code_path, "rb") as image_file:
        encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
    
    params = {
        "from": "Attendance <onboarding@resend.dev>",
        "to": email,
        "subject": "Student Registration Successful",
        "html": f"<p>Dear {name},</p><p>Your registration was successful.</p>",
        "attachments": [
            {
                "filename": "qr_code.png",
                "content": encoded_string,  
                "type": "image/png"
            }
        ],
    }
    try:
        response = resend.Emails.send(params)
        print("Email sent:", response)
    except Exception as e:
        print("Failed to send email:", e)

# Routes
@app.route('/')
def index():
    search_query = request.args.get('query')
    students = Student.query.filter(Student.name.contains(search_query)).all() if search_query else Student.query.all()
    return render_template('index.html', students=students)

@app.route('/add_student', methods=['GET', 'POST'])
def add_student():
    if request.method == 'POST':
        name = request.form['name']
        roll_number = request.form['roll_number']
        class_name = request.form['class_name']
        email = request.form['email']  # Capture email from form data

        # Generate QR code
        check_in_url = url_for('check_in', roll_number=roll_number, _external=True)
        qr = qrcode.make(check_in_url)
        
        qr_code_dir = 'static/qr_codes'
        ensure_directory_exists(qr_code_dir)
        qr_code_path = os.path.join(qr_code_dir, f'{roll_number}.png')
        qr.save(qr_code_path)
        
        # Add student to the database
        student = Student(name=name, roll_number=roll_number, class_name=class_name, email=email, qr_code_path=qr_code_path)
        db.session.add(student)
        try:
            db.session.commit()
            flash("Student added successfully.", "success")

            # Send email with QR code
            send_email_with_qr(email, name, qr_code_path)
            
            return redirect(url_for('student_details', student_id=student.id))
        except IntegrityError:
            db.session.rollback()
            flash("Roll number or email already exists. Please try again with unique values.", "danger")
            return redirect(url_for('add_student'))
    return render_template('add_student.html')


@app.route('/student_details/<int:student_id>', methods=['GET'])
def student_details(student_id):
    student = Student.query.get_or_404(student_id)
    return render_template('student_details.html', student=student)

@app.route('/mark_attendance', methods=['GET', 'POST'])
def mark_attendance():
    students = Student.query.all()
    if request.method == 'POST':
        date = request.form['date']
        date_obj = datetime.strptime(date, '%Y-%m-%d').date()

        for student in students:
            existing_attendance = Attendance.query.filter_by(student_id=student.id, date=date_obj).first()
            if not existing_attendance:
                status = request.form.get(f'status_{student.id}', 'absent')
                attendance = Attendance(date=date_obj, student_id=student.id, status=status)
                db.session.add(attendance)
        try:
            db.session.commit()
            flash("Attendance marked successfully.", "success")
        except IntegrityError:
            db.session.rollback()
            flash("Attendance for some students already exists for this date.", "danger")
    return render_template('mark_attendance.html', students=students)

@app.route('/check_in/<roll_number>', methods=['POST', 'GET'])
def check_in(roll_number):
    student = Student.query.filter_by(roll_number=roll_number).first()
    if not student:
        return redirect(url_for('error', message='Student not found')) 

    date_now = datetime.now(timezone.utc).date()
    attendance = Attendance.query.filter_by(student_id=student.id, date=date_now).first()
    
    if not attendance:
        attendance = Attendance(date=date_now, student_id=student.id, status='present')
        db.session.add(attendance)
        db.session.commit()

    return redirect(url_for('success', student_name=student.name)) 

@app.route('/success')
def success():
    student_name = request.args.get('student_name')
    return render_template('success.html', student_name=student_name)

@app.route('/error')
def error():
    message = request.args.get('message')
    return render_template('error.html', message=message)

@app.route('/view_records', methods=['GET'])
def view_records():
    search_query = request.args.get('query')
    students = Student.query.filter(Student.name.contains(search_query)).all() if search_query else Student.query.all()
    attendance_records = Attendance.query.options(joinedload(Attendance.student)).filter(
        Attendance.student.has(Student.name.contains(search_query))
    ).all() if search_query else Attendance.query.options(joinedload(Attendance.student)).all()
    
    return render_template('view_records.html', students=students, attendance_records=attendance_records)

@app.route('/generate_report', methods=['GET'])
def generate_report():
    ensure_directory_exists("reports")

    # Fetch all attendance records and group by date
    attendance_records = Attendance.query.all()
    
    # Handle empty report case
    if not attendance_records:
        return render_template('error.html', message="No attendance records found for the report.")
    
    # Prepare the date for rendering
    report_date = attendance_records[0].date  
    total_records = len(attendance_records)

    rendered = render_template('report_template.html', 
                               attendance_records=attendance_records,
                               date=report_date,
                               total_records=total_records)
    
    pdf_path = "reports/attendance_report.pdf"
    with open(pdf_path, "wb") as pdf_file:
        pisa.CreatePDF(rendered, dest=pdf_file, link_callback=link_callback)
    
    return send_file(pdf_path, as_attachment=True)

@app.errorhandler(404)
def page_not_found(error):
    return render_template('404.html'), 404

@app.errorhandler(Exception)
def handle_exception(error):
    logging.error(f"An error occurred: {error}")
    return render_template('general_error.html', error=error), 500

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", debug=True)

