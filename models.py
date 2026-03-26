from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone

db = SQLAlchemy()


class Voter(db.Model):
    __tablename__ = 'voters'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    voter_id = db.Column(db.String(20), unique=True, nullable=False)
    phone = db.Column(db.String(15), nullable=False)
    email = db.Column(db.String(100), nullable=True) # Added email field
    voter_image = db.Column(db.String(255), nullable=True)  # Path to stored image
    voter_image_hash = db.Column(db.String(64), unique=True, nullable=True) # SHA-256 hash of image
    face_descriptor = db.Column(db.Text, nullable=True)  # JSON string of face descriptor
    has_voted = db.Column(db.Boolean, default=False)
    registered_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    is_authenticated = db.Column(db.Boolean, default=False)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'voter_id': self.voter_id,
            'phone': self.phone,
            'email': self.email,
            'has_voted': self.has_voted,
            'has_face': self.face_descriptor is not None,
            'voter_image': self.voter_image,
            'registered_at': self.registered_at.isoformat()
        }


class Candidate(db.Model):
    __tablename__ = 'candidates'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    party = db.Column(db.String(100), nullable=False)
    symbol = db.Column(db.String(10), nullable=False)  # Emoji symbol
    description = db.Column(db.String(255), default='')
    vote_count = db.Column(db.Integer, default=0)

    def to_dict(self, show_votes=False):
        data = {
            'id': self.id,
            'name': self.name,
            'party': self.party,
            'symbol': self.symbol,
            'description': self.description
        }
        if show_votes:
            data['vote_count'] = self.vote_count
        return data


class Vote(db.Model):
    __tablename__ = 'votes'

    id = db.Column(db.Integer, primary_key=True)
    voter_id = db.Column(db.String(20), nullable=False)
    encrypted_vote = db.Column(db.String(256), nullable=False)
    vote_hash = db.Column(db.String(64), nullable=False)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    fraud_score = db.Column(db.Float, default=0.0)
    is_flagged = db.Column(db.Boolean, default=False)
    voting_image = db.Column(db.String(255), nullable=True) # Path to image uploaded during voting

    def to_dict(self):
        return {
            'id': self.id,
            'voter_id': self.voter_id,
            'encrypted_vote': self.encrypted_vote,
            'vote_hash': self.vote_hash,
            'timestamp': self.timestamp.isoformat(),
            'fraud_score': self.fraud_score,
            'is_flagged': self.is_flagged,
            'voting_image': self.voting_image
        }


class Election(db.Model):
    __tablename__ = 'elections'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), default='General Election 2026')
    is_active = db.Column(db.Boolean, default=False)
    started_at = db.Column(db.DateTime, nullable=True)
    ended_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'is_active': self.is_active,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'ended_at': self.ended_at.isoformat() if self.ended_at else None
        }


class OTPRecord(db.Model):
    __tablename__ = 'otp_records'

    id = db.Column(db.Integer, primary_key=True)
    voter_id = db.Column(db.String(20), nullable=False)
    otp_code = db.Column(db.String(6), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    is_used = db.Column(db.Boolean, default=False)
