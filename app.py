import os
import json
import hashlib
import secrets
import random
import base64
import io
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import jwt

import sys
from pathlib import Path

# Add necessary directories to Python PATH robustly
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

try:
    from backend.models import db, Voter, Candidate, Vote, Election, OTPRecord
    from face_recognition_module.fraud_detector import FraudDetector, prepare_features
    from face_recognition_module.face_engine import verify_face_match
except ImportError:
    # Fallback for different execution contexts
    from models import db, Voter, Candidate, Vote, Election, OTPRecord
    from fraud_detector import FraudDetector, prepare_features
    from face_engine import verify_face_match

# ─── App Configuration ───────────────────────────────────────────────
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
FRONTEND_DIR = os.path.join(BASE_DIR, 'frontend')
DB_DIR = os.path.join(BASE_DIR, 'database')

# Adjusted to define static folder manually to avoid root conflict
app = Flask(__name__, static_folder=None)
app.config['SECRET_KEY'] = secrets.token_hex(32)

# Ensure directories exist
os.makedirs(DB_DIR, exist_ok=True)
os.makedirs(os.path.join(FRONTEND_DIR, 'uploads'), exist_ok=True)

# Adjusted DB URI to point to the new 'database' directory
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(DB_DIR, 'securevote_v2.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

CORS(app)
db.init_app(app)

fraud_detector = FraudDetector()

ADMIN_USERNAME = 'admin'
ADMIN_PASSWORD = 'admin123'

# --- SMTP Config (Optional) ---
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_EMAIL = os.environ.get("SMTP_EMAIL", "")     # For testing: set these in env
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")

def send_otp_email(to_email, otp_code, voter_name):
    """Sends OTP via email. Falls back to terminal print if no credentials."""
    subject = f"SecureVote: Your Voting OTP - {otp_code}"
    body = f"""
    Hello {voter_name},

    Your one-time password (OTP) for the current election is: {otp_code}

    This code will expire in 10 minutes. Please enter this code in the SecureVote terminal to finalize your vote.

    If you did not request this, please ignore this email.
    """
    
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        print("="*40)
        print(f" [SIMULATION MODE] No SMTP Credentials Found")
        print(f" To: {to_email}")
        print(f" OTP: {otp_code}")
        print("="*40)
        return True, True  # success=True, is_stub=True

    try:
        print(f"[SMTP] Attempting to send real email to {to_email}...")
        msg = MIMEMultipart()
        msg['From'] = SMTP_EMAIL
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"[SMTP] Email successfully sent to {to_email}")
        return True, False # success=True, is_stub=False
    except Exception as e:
        print(f"[SMTP Error] Failed to send email: {e}")
        return False, False # success=False, is_stub=False


@app.route('/api/test-email', methods=['POST'])
def test_email_api():
    """Manually test SMTP settings."""
    data = request.get_json()
    test_email = data.get('email')
    if not test_email:
        return jsonify({'error': 'Email is required for testing'}), 400
    
    print(f"[Admin] Testing email settings with: {test_email}")
    success = send_otp_email(test_email, "123456", "Test User")
    
    if success:
        return jsonify({'message': f'Test email triggered successfully. Outcome: {"Sent" if SMTP_EMAIL else "Stub Output"}'})
    else:
        return jsonify({'error': 'Failed to send test email. Check server console for errors.'}), 500


# ─── JWT Helpers ──────────────────────────────────────────────────────
def generate_token(voter_id, stage='login'):
    payload = {
        'voter_id': voter_id,
        'stage': stage,
        'exp': datetime.now(timezone.utc) + timedelta(hours=1)
    }
    return jwt.encode(payload, app.config['SECRET_KEY'], algorithm='HS256')


def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        if not token:
            return jsonify({'error': 'Token is missing'}), 401
        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            request.voter_id = data['voter_id']
            request.auth_stage = data.get('stage', 'login')
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token expired'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Invalid token'}), 401
        return f(*args, **kwargs)
    return decorated


# ─── Encryption Helpers ──────────────────────────────────────────────
def encrypt_vote(voter_id, candidate_id):
    salt = secrets.token_hex(16)
    data = f"{voter_id}:{candidate_id}:{salt}:{datetime.now(timezone.utc).isoformat()}"
    encrypted = hashlib.sha256(data.encode()).hexdigest()
    return encrypted, salt


def hash_vote(encrypted_vote):
    return hashlib.sha256(encrypted_vote.encode()).hexdigest()


# ─── Static File Serving ─────────────────────────────────────────────
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

@app.route('/')
def serve_index():
    return send_from_directory(FRONTEND_DIR, 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    if os.path.exists(os.path.join(FRONTEND_DIR, path)):
        return send_from_directory(FRONTEND_DIR, path)
    return jsonify({"error": "File not found"}), 404

@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


# ─── Voter Registration ──────────────────────────────────────────────
@app.route('/api/register', methods=['POST'])
def register():
    data = request.get_json()
    name = data.get('name', '').strip().upper()  # Enforce CAPS
    phone = data.get('phone', '').strip()
    email = data.get('email', '').strip().lower()
    face_descriptor = data.get('face_descriptor')
    voter_image_data = data.get('voter_image') # Base64 string

    if not all([name, phone, email, voter_image_data]):
        return jsonify({'error': 'Name, Phone, Email, and Voter Image are required'}), 400
    
    # Relaxed Validation
    # Strip spaces and dashes from phone for length check
    clean_phone = phone.replace(' ', '').replace('-', '')
    if not clean_phone.startswith('+91') or len(clean_phone) != 13:
        return jsonify({'error': 'Phone number must start with +91 followed by 10 digits.'}), 400
    phone = clean_phone # Store cleaned version
    
    if not email.endswith('@gmail.com'):
        return jsonify({'error': 'Email must be a valid @gmail.com address.'}), 400

    # Handle Voter Image
    try:
        if ',' in voter_image_data:
            header, encoded = voter_image_data.split(',', 1)
        else:
            encoded = voter_image_data
        
        image_bytes = base64.b64decode(encoded)
        image_hash = hashlib.sha256(image_bytes).hexdigest()

        # Check for duplicate image
        if Voter.query.filter_by(voter_image_hash=image_hash).first():
            return jsonify({'error': 'This voter image has already been used for registration.'}), 409

        # Generate a unique voter ID based on hash short form
        image_hash_str = str(image_hash)
        voter_id = f"V-{image_hash_str[:8].upper()}"
        
        # Check if this ID exists (unlikely collision)
        while Voter.query.filter_by(voter_id=voter_id).first():
            voter_id = f"V-{secrets.token_hex(4).upper()}"

        # Save image
        image_filename = f"{voter_id}.jpg"
        image_path = os.path.join(FRONTEND_DIR, 'uploads', image_filename)
        os.makedirs(os.path.dirname(image_path), exist_ok=True)
        with open(image_path, 'wb') as f:
            f.write(image_bytes)

    except Exception as e:
        return jsonify({'error': f'Invalid image data: {str(e)}'}), 400

    voter = Voter(
        name=name,
        voter_id=voter_id,
        phone=phone,
        email=email,
        voter_image=f"/uploads/{image_filename}",
        voter_image_hash=image_hash,
        face_descriptor=json.dumps(face_descriptor) if face_descriptor else None
    )
    
    # Also save the captured face if provided
    face_image_data = data.get('face_image')
    if face_image_data:
        try:
            if ',' in face_image_data:
                _, face_encoded = face_image_data.split(',', 1)
            else:
                face_encoded = face_image_data
            face_bytes = base64.b64decode(face_encoded)
            face_filename = f"{voter_id}_face.jpg"
            face_path = os.path.join(FRONTEND_DIR, 'uploads', face_filename)
            os.makedirs(os.path.dirname(face_path), exist_ok=True)
            with open(face_path, 'wb') as f:
                f.write(face_bytes)
            # Use this captured face as the reference for future voting
            voter.voter_image = f"/uploads/{face_filename}"
        except Exception as e:
            print(f"[App] Error saving face image: {e}")
            pass # Fallback to original voter image
            
    db.session.add(voter)
    db.session.commit()

    return jsonify({
        'message': 'Registration successful',
        'voter': voter.to_dict(),
        'voter_id': voter_id
    }), 201


# ─── Login (Voter ID Verification) ───────────────────────────────────
@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    voter_image_data = data.get('voter_image') # Base64 string identification

    if not voter_image_data:
        return jsonify({'error': 'Voter image is required for identification'}), 400

    try:
        if ',' in voter_image_data:
            _, encoded = voter_image_data.split(',', 1)
        else:
            encoded = voter_image_data
        
        image_bytes = base64.b64decode(encoded)
        image_hash = hashlib.sha256(image_bytes).hexdigest()

        voter = Voter.query.filter_by(voter_image_hash=image_hash).first()
        if not voter:
            return jsonify({'error': 'Voter image not recognized. Please register first.'}), 404

        if voter.has_voted:
            return jsonify({'error': 'You have already cast your vote.'}), 403

        election = Election.query.first()
        if not election or not election.is_active:
            return jsonify({'error': 'No active election at this time.'}), 403

        # Jump directly to auth_pending instead of face_pending
        token = generate_token(voter.voter_id, stage='auth_pending')
        
        return jsonify({
            'message': f'Welcome, {voter.name}! Please choose an authentication method.',
            'token': token,
            'voter_name': voter.name,
            'stage': 'auth_pending',
            'voter_id': voter.voter_id
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/login-email', methods=['POST'])
def login_email():
    """Voter identification via email (alternative to image)."""
    try:
        data = request.get_json()
        email = data.get('email', '').lower().strip()
        
        if not email:
            return jsonify({'error': 'Email is required'}), 400
            
        voter = Voter.query.filter_by(email=email).first()
        if not voter:
            return jsonify({'error': 'Voter not found with this email'}), 404
            
        if voter.has_voted:
            return jsonify({'error': 'You have already cast your vote.'}), 403

        # Check for active election
        election = Election.query.filter_by(is_active=True).first()
        if not election:
            return jsonify({'error': 'No active election at this time.'}), 403
            
        token = generate_token(voter.voter_id, stage='auth_pending')
        
        return jsonify({
            'message': f'Welcome, {voter.name}! Please choose an authentication method.',
            'token': token,
            'voter_name': voter.name,
            'stage': 'auth_pending',
            'voter_id': voter.voter_id
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─── OTP Send ────────────────────────────────────────────────────────
@app.route('/api/send-otp', methods=['POST'])
@token_required
def send_otp():
    voter_id = request.voter_id
    otp_code = str(random.randint(100000, 999999))

    # Invalidate old OTPs
    OTPRecord.query.filter_by(voter_id=voter_id, is_used=False).update({'is_used': True})

    otp = OTPRecord(voter_id=voter_id, otp_code=otp_code)
    db.session.add(otp)
    db.session.commit()

    voter = Voter.query.filter_by(voter_id=voter_id).first()
    masked_phone = voter.phone[:3] + '****' + voter.phone[-3:]

    return jsonify({
        'message': f'OTP sent to {masked_phone}',
        'otp_demo': otp_code  # In production, remove this; send via SMS
    })


# ─── OTP Verify ──────────────────────────────────────────────────────
@app.route('/api/verify-otp', methods=['POST'])
@token_required
def verify_otp():
    data = request.get_json()
    otp_input = data.get('otp', '').strip()
    voter_id = request.voter_id

    if not otp_input:
        return jsonify({'error': 'OTP is required'}), 400

    otp_record = OTPRecord.query.filter_by(
        voter_id=voter_id,
        otp_code=otp_input,
        is_used=False
    ).first()

    if not otp_record:
        return jsonify({'error': 'Invalid or expired OTP'}), 401

    # Check if OTP is older than 5 minutes
    if datetime.now(timezone.utc) - otp_record.created_at > timedelta(minutes=5):
        otp_record.is_used = True
        db.session.commit()
        return jsonify({'error': 'OTP has expired. Please request a new one.'}), 401

    otp_record.is_used = True
    db.session.commit()

    voter = Voter.query.filter_by(voter_id=voter_id).first()
    has_face = voter.face_descriptor is not None

    token = generate_token(voter_id, stage='voter_image_pending')

    return jsonify({
        'message': 'OTP verified successfully!',
        'token': token,
        'has_face': has_face,
        'stage': 'voter_image_pending'
    })


# ─── Voter Image Verification ────────────────────────────────────────
@app.route('/api/verify-voter-image', methods=['POST'])
@token_required
def verify_voter_image():
    data = request.get_json()
    voter_image_data = data.get('voter_image')
    voter_id = request.voter_id

    if not voter_image_data:
        return jsonify({'error': 'Voter image is required'}), 400

    voter = Voter.query.filter_by(voter_id=voter_id).first()
    if not voter:
        return jsonify({'error': 'Voter not found'}), 404

    try:
        if ',' in voter_image_data:
            _, encoded = voter_image_data.split(',', 1)
        else:
            encoded = voter_image_data
        
        image_bytes = base64.b64decode(encoded)
        image_hash = hashlib.sha256(image_bytes).hexdigest()

        if image_hash == voter.voter_image_hash:
            token = generate_token(voter_id, stage='face_pending' if voter.face_descriptor else 'authenticated')
            return jsonify({
                'message': 'Voter image verified successfully!',
                'token': token,
                'stage': 'face_pending' if voter.face_descriptor else 'authenticated'
            })
        else:
            return jsonify({'error': 'Voter image does not match the registered record.'}), 401
    except Exception as e:
        return jsonify({'error': f'Invalid image data: {str(e)}'}), 400


# ─── Face Verification (OpenCV) ───────────────────────────────────────────────
@app.route('/api/verify-face', methods=['POST'])
@token_required
def verify_face():
    data = request.get_json()
    live_image_data = data.get('live_image')
    voter_id = request.voter_id

    if not live_image_data:
        return jsonify({'error': 'Live webcam image is required for OpenCV face verification'}), 400

    voter = Voter.query.filter_by(voter_id=voter_id).first()
    if not voter or not voter.voter_image:
        return jsonify({'error': 'No registered face image found'}), 404

    # Build absolute path to the registered image
    rel_path = voter.voter_image.lstrip('/')
    registered_image_path = os.path.abspath(os.path.join(FRONTEND_DIR, rel_path))
    
    # Run OpenCV Face Verification (Threshold tuned for Correlation)
    is_match, match_score, msg = verify_face_match(registered_image_path, live_image_data, threshold=0.45)

    if is_match:
        token = generate_token(voter_id, stage='authenticated')
        return jsonify({
            'message': 'Face verified successfully via OpenCV!',
            'token': token,
            'match_score': match_score
        })
    else:
        return jsonify({
            'error': f'Face verification failed: {msg}',
            'match_score': match_score
        }), 401



# ─── Skip Face (for voters without face data) ────────────────────────
@app.route('/api/skip-face', methods=['POST'])
@token_required
def skip_face():
    voter_id = request.voter_id
    voter = Voter.query.filter_by(voter_id=voter_id).first()

    if voter and voter.face_descriptor:
        return jsonify({'error': 'Face verification is required for this voter'}), 403

    token = generate_token(voter_id, stage='authenticated')
    return jsonify({
        'message': 'Proceeding without face verification.',
        'token': token
    })


# ─── Voting OTP Flow (Advanced Level) ────────────────────────────────
@app.route('/api/send-voting-otp', methods=['POST'])
@token_required
def send_voting_otp():
    voter_id = request.voter_id
    voter = Voter.query.filter_by(voter_id=voter_id).first()
    if not voter:
        return jsonify({'error': 'Voter not found'}), 404

    otp_code = str(random.randint(100000, 999999))
    
    # Invalidate old OTPs for this voter
    OTPRecord.query.filter_by(voter_id=voter_id, is_used=False).update({'is_used': True})

    otp = OTPRecord(voter_id=voter_id, otp_code=otp_code)
    db.session.add(otp)
    db.session.commit()

    success, is_stub = send_otp_email(voter.email, otp_code, voter.name)
    
    if is_stub:
        message = f"SIMULATION: OTP printed to server terminal for {voter.email}"
    elif success:
        message = f"OTP successfully sent to {voter.email}"
    else:
        message = "Critical Error: SMTP failure. Check server logs."

    return jsonify({
        'message': message,
        'otp_sent': success,
        'is_simulation': is_stub,
        'otp_code': otp_code if is_stub else None # ONLY return if simulation
    })

@app.route('/api/verify-voting-otp', methods=['POST'])
@token_required
def verify_voting_otp():
    data = request.get_json()
    otp_input = data.get('otp', '').strip()
    voter_id = request.voter_id

    otp_record = OTPRecord.query.filter_by(
        voter_id=voter_id,
        otp_code=otp_input,
        is_used=False
    ).first()

    if not otp_record:
        return jsonify({'error': 'Invalid or expired OTP'}), 401

    # Fix: Ensure comparison is between offset-aware datetimes
    record_created_at = otp_record.created_at
    if record_created_at.tzinfo is None:
        record_created_at = record_created_at.replace(tzinfo=timezone.utc)

    if datetime.now(timezone.utc) - record_created_at > timedelta(minutes=10):
        otp_record.is_used = True
        db.session.commit()
        return jsonify({'error': 'OTP has expired (10 min limit)'}), 401

    otp_record.is_used = True
    db.session.commit()

    # Grant 'authenticated' stage
    token = generate_token(voter_id, stage='authenticated')
    
    return jsonify({
        'message': 'OTP verified! Casting your vote...',
        'token': token
    })


# ─── Get Candidates ──────────────────────────────────────────────────
@app.route('/api/candidates', methods=['GET'])
@token_required
def get_candidates():
    if request.auth_stage not in ['auth_pending', 'authenticated']:
        return jsonify({'error': 'Complete initial identification first'}), 403

    candidates = Candidate.query.all()
    return jsonify({
        'candidates': [c.to_dict(show_votes=False) for c in candidates]
    })


# ─── Cast Vote ───────────────────────────────────────────────────────
@app.route('/api/vote', methods=['POST'])
@token_required
def cast_vote():
    if request.auth_stage != 'authenticated':
        return jsonify({'error': 'Complete all authentication steps first'}), 403

    data = request.get_json()
    candidate_id = data.get('candidate_id')
    voter_id = request.voter_id

    if not candidate_id:
        return jsonify({'error': 'Candidate selection is required'}), 400

    voter = Voter.query.filter_by(voter_id=voter_id).first()
    if not voter:
        return jsonify({'error': 'Voter not found'}), 404

    if voter.has_voted:
        return jsonify({'error': 'You have already cast your vote'}), 403

    election = Election.query.first()
    if not election or not election.is_active:
        return jsonify({'error': 'No active election'}), 403

    candidate = Candidate.query.get(candidate_id)
    if not candidate:
        return jsonify({'error': 'Invalid candidate'}), 404

    # Encrypt and store vote
    encrypted_vote, _ = encrypt_vote(voter_id, candidate_id)
    vote_hash_value = hash_vote(encrypted_vote)

    # Save the current session's voting image
    voting_image_data = data.get('voting_image') # The image used to identify
    voting_image_path = None
    if voting_image_data:
        try:
            if ',' in voting_image_data:
                _, encoded = voting_image_data.split(',', 1)
            else:
                encoded = voting_image_data
            image_bytes = base64.b64decode(encoded)
            filename = f"vote_{voter_id}_{secrets.token_hex(4)}.jpg"
            save_path = os.path.join(os.path.dirname(__file__), '..', 'frontend', 'uploads', filename)
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            with open(save_path, 'wb') as f:
                f.write(image_bytes)
            voting_image_path = f'/uploads/{filename}'
        except Exception as e:
            print(f"Error saving voting image: {e}")

    # ML Fraud Detection
    try:
        features = prepare_features(voter, datetime.now(timezone.utc))
        fraud_score, is_flagged = fraud_detector.predict(features)
    except Exception as e:
        print(f"Non-critical Error in Fraud Detection: {e}")
        fraud_score, is_flagged = 0.0, False

    vote = Vote(
        voter_id=voter_id,
        encrypted_vote=encrypted_vote,
        vote_hash=vote_hash_value,
        fraud_score=fraud_score,
        is_flagged=is_flagged,
        voting_image=voting_image_path
    )
    db.session.add(vote)

    # Update vote count and voter status
    candidate.vote_count += 1
    voter.has_voted = True
    db.session.commit()

    return jsonify({
        'message': 'Your vote has been recorded securely!',
        'vote_hash': vote_hash_value,
        'timestamp': datetime.now(timezone.utc).isoformat()
    })


# ─── Election Results (Public) ────────────────────────────────────────
@app.route('/api/results', methods=['POST'])
def get_results():
    data = request.get_json() or {}
    password = data.get('password')

    if password != ADMIN_PASSWORD:
        return jsonify({'error': 'Incorrect password. Results access restricted.'}), 401

    election = Election.query.first()
    if not election:
        return jsonify({'error': 'No election found'}), 404

    # Results can be viewed with password regardless of election status
    # if election.is_active:
    #     return jsonify({'error': 'Election is still in progress.'}), 403

    candidates = Candidate.query.order_by(Candidate.vote_count.desc()).all()
    total_votes = sum(c.vote_count for c in candidates)

    return jsonify({
        'election': election.to_dict(),
        'results': [c.to_dict(show_votes=True) for c in candidates],
        'total_votes': total_votes
    })


# ─── Admin: Login ─────────────────────────────────────────────────────
@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    data = request.get_json()
    username = data.get('username', '')
    password = data.get('password', '')

    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        token = jwt.encode(
            {'admin': True, 'exp': datetime.now(timezone.utc) + timedelta(hours=4)},
            app.config['SECRET_KEY'], algorithm='HS256'
        )
        return jsonify({'message': 'Admin login successful', 'token': token})
    return jsonify({'error': 'Invalid credentials'}), 401


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            if not data.get('admin'):
                raise Exception()
        except Exception:
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated


# ─── Admin: Dashboard Stats ──────────────────────────────────────────
@app.route('/api/admin/stats', methods=['GET'])
@admin_required
def admin_stats():
    total_voters = Voter.query.count()
    voted_count = Voter.query.filter_by(has_voted=True).count()
    total_votes = Vote.query.count()
    flagged_votes = Vote.query.filter_by(is_flagged=True).count()
    election = Election.query.first()

    candidates = Candidate.query.order_by(Candidate.vote_count.desc()).all()

    return jsonify({
        'total_voters': total_voters,
        'voted_count': voted_count,
        'pending_voters': total_voters - voted_count,
        'total_votes': total_votes,
        'flagged_votes': flagged_votes,
        'election': election.to_dict() if election else None,
        'candidates': [c.to_dict(show_votes=True) for c in candidates]
    })


# ─── Admin: Start Election ───────────────────────────────────────────
@app.route('/api/admin/start-election', methods=['POST'])
@admin_required
def start_election():
    election = Election.query.first()
    if not election:
        election = Election(name='General Election 2026')
        db.session.add(election)

    election.is_active = True
    election.started_at = datetime.now(timezone.utc)
    election.ended_at = None
    db.session.commit()

    return jsonify({'message': 'Election started!', 'election': election.to_dict()})


# ─── Admin: Stop Election ────────────────────────────────────────────
@app.route('/api/admin/stop-election', methods=['POST'])
@admin_required
def stop_election():
    election = Election.query.first()
    if not election or not election.is_active:
        return jsonify({'error': 'No active election'}), 400

    election.is_active = False
    election.ended_at = datetime.now(timezone.utc)
    db.session.commit()

    return jsonify({'message': 'Election stopped!', 'election': election.to_dict()})


# ─── Admin: Reset Election ───────────────────────────────────────────
@app.route('/api/admin/reset', methods=['POST'])
@admin_required
def reset_election():
    Vote.query.delete()
    OTPRecord.query.delete()
    Voter.query.update({Voter.has_voted: False})
    for c in Candidate.query.all():
        c.vote_count = 0
    election = Election.query.first()
    if election:
        election.is_active = False
        election.started_at = None
        election.ended_at = None
    db.session.commit()

    return jsonify({'message': 'Election has been reset.'})


# ─── Admin: Voter List ───────────────────────────────────────────────
@app.route('/api/admin/delete-voter-by-image', methods=['POST'])
@admin_required
def delete_voter_by_image():
    data = request.get_json()
    voter_image_data = data.get('voter_image')
    
    if not voter_image_data:
        return jsonify({'error': 'Voter image is required'}), 400
        
    try:
        if ',' in voter_image_data:
            _, encoded = voter_image_data.split(',', 1)
        else:
            encoded = voter_image_data
        
        image_bytes = base64.b64decode(encoded)
        image_hash = hashlib.sha256(image_bytes).hexdigest()
        
        voter = Voter.query.filter_by(voter_image_hash=image_hash).first()
        if not voter:
            return jsonify({'error': 'Voter not found with this image'}), 404
            
        voter_id = voter.voter_id
        
        # Delete associated image files
        try:
            # Main image
            if voter.voter_image:
                rel_img_path = voter.voter_image.lstrip('/')
                abs_img_path = os.path.join(FRONTEND_DIR, rel_img_path)
                if os.path.exists(abs_img_path):
                    os.remove(abs_img_path)
            
            # Secondary face image if exists
            face_img_path = os.path.join(FRONTEND_DIR, 'uploads', f"{voter_id}_face.jpg")
            if os.path.exists(face_img_path):
                os.remove(face_img_path)
        except Exception as e:
            print(f"[Cleanup] Error deleting files: {e}")

        # Delete associated records
        Vote.query.filter_by(voter_id=voter_id).delete()
        OTPRecord.query.filter_by(voter_id=voter_id).delete()
        
        db.session.delete(voter)
        db.session.commit()
        
        return jsonify({'message': f'Voter {voter_id} deleted successfully from database and disk'})
    except Exception as e:
        return jsonify({'error': f'Deletion failed: {str(e)}'}), 500

# ─── Admin: Candidate Management ───────────────────────────────────────
@app.route('/api/admin/candidates', methods=['POST'])
@admin_required
def add_candidate():
    data = request.get_json()
    name = data.get('name', '').strip()
    party = data.get('party', '').strip()
    symbol = data.get('symbol', '').strip()
    description = data.get('description', '').strip()

    if not all([name, party, symbol]):
        return jsonify({'error': 'Name, Party, and Symbol are required'}), 400

    new_candidate = Candidate(
        name=name, party=party, symbol=symbol, description=description
    )
    db.session.add(new_candidate)
    db.session.commit()

    return jsonify({'message': 'Candidate added successfully', 'candidate': new_candidate.to_dict()}), 201

@app.route('/api/admin/candidates/<int:candidate_id>', methods=['DELETE'])
@admin_required
def delete_candidate(candidate_id):
    candidate = Candidate.query.get(candidate_id)
    if not candidate:
        return jsonify({'error': 'Candidate not found'}), 404

    # Prevent deletion if votes exist to protect integrity
    if candidate.vote_count > 0:
        return jsonify({'error': 'Cannot delete a candidate with existing votes'}), 400

    db.session.delete(candidate)
    db.session.commit()
    return jsonify({'message': 'Candidate deleted successfully'})

# ─── Initialize Database ─────────────────────────────────────────────
def init_db():
    with app.app_context():
        db.create_all()

        # Seed candidates if none exist
        if Candidate.query.count() == 0:
            candidates = [
                Candidate(name='Aarav Sharma', party='Progressive Alliance', symbol='🌟', description='Building a brighter future for all citizens'),
                Candidate(name='Priya Patel', party='Green India Party', symbol='🌿', description='Sustainable development and environmental protection'),
                Candidate(name='Rahul Verma', party='Digital India Front', symbol='💻', description='Technology-driven governance and innovation'),
                Candidate(name='Sneha Reddy', party='People\'s Welfare Party', symbol='🤝', description='Healthcare, education, and social equity'),
                Candidate(name='Vikram Singh', party='National Development', symbol='🏗️', description='Infrastructure and economic growth'),
            ]
            db.session.add_all(candidates)

        # Create default election
        if Election.query.count() == 0:
            election = Election(name='General Election 2026', is_active=False)
            db.session.add(election)

        db.session.commit()


if __name__ == '__main__':
    init_db()
    print("\n" + "=" * 60)
    print("  [SecureVote] Digital Voting System")
    print("  [SECURE] Multi-Factor Authentication Enabled")
    print("  [URL] Open http://localhost:5000 in your browser")
    print("  [ADMIN] username=admin, password=admin123")
    print("=" * 60 + "\n")
    app.run(debug=True, port=5000)
