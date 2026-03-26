import numpy as np
import pandas as pd
try:
    from sklearn.ensemble import IsolationForest
except ImportError:
    # Minimal mock for IsolationForest if scikit-learn is not installed
    class IsolationForest:
        def __init__(self, *args, **kwargs): pass
        def fit(self, *args, **kwargs): return self
        def predict(self, X): return np.ones(len(X)) # Always return 1 (normal)
        def decision_function(self, X): return np.zeros(len(X)) # Normal score
import joblib
import os

class FraudDetector:
    def __init__(self, model_path='fraud_model.joblib'):
        self.model_path = model_path
        self.model = self.load_model()
        
    def load_model(self):
        if os.path.exists(self.model_path):
            return joblib.load(self.model_path)
        else:
            # Initialize with a fresh model if not exists
            return IsolationForest(contamination=0.1, random_state=42)

    def train_or_update(self, data_points):
        """
        Trains/Updates the model with new data.
        data_points: List of lists [time_to_vote, registration_age_minutes, consistency_score]
        """
        if not data_points:
            return
        
        df = pd.DataFrame(data_points, columns=['time_to_vote', 'reg_age', 'consistency'])
        
        # In a real scenario, we'd append to historical data
        self.model.fit(df)
        joblib.dump(self.model, self.model_path)

    def predict(self, feature_vector):
        """
        Predicts if a vote is suspicious.
        feature_vector: [time_to_vote, reg_age, consistency]
        Returns: (fraud_score, is_flagged)
        """
        # IsolationForest returns -1 for decoys (outliers) and 1 for inliers
        # We transform this to 0 (normal) to 1 (fraud)
        
        # If model isn't trained yet, return safe defaults
        try:
            prediction = self.model.predict([feature_vector])[0]
            # decision_function returns raw anomaly score (lower is more abnormal)
            score = self.model.decision_function([feature_vector])[0]
            
            # Normalize score roughly between 0 and 1
            # IsolationForest score is typically in range [-0.5, 0.5]
            normalized_score = np.clip(1.0 - (score + 0.5), 0, 1)
            is_flagged = bool(prediction == -1)
            
            return float(normalized_score), is_flagged
        except:
            # Model not fitted yet
            return 0.0, False

# Helper function to extract features for a vote
def prepare_features(voter, vote_time):
    # time_to_vote: seconds since login (simulated for now or calculated)
    # reg_age: minutes since registration
    from datetime import timezone
    try:
        if not voter.registered_at:
            reg_age = 0.0
        else:
            # Ensure both are either aware or naive for safe subtraction
            v_reg = voter.registered_at
            if v_reg.tzinfo is None and vote_time.tzinfo is not None:
                v_reg = v_reg.replace(tzinfo=timezone.utc)
            elif v_reg.tzinfo is not None and vote_time.tzinfo is None:
                vote_time = vote_time.replace(tzinfo=timezone.utc)
            
            reg_age = (vote_time - v_reg).total_seconds() / 60.0
    except Exception as e:
        print(f"Error in prepare_features: {e}")
        reg_age = 0.0
    
    # Placeholder for more complex behavioral features
    try:
        consistency = float(len(voter.name) % 10) / 10.0 if voter.name else 0.5
    except:
        consistency = 0.5
    
    return [30.0, reg_age, consistency] # [30 seconds average, reg_age, consistency]
