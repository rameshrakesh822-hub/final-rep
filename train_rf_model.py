import pandas as pd
import pickle
from sklearn.ensemble import RandomForestClassifier

# Load dataset
df = pd.read_csv("coach_condition_ml_dataset.csv")

X = df[["km_run", "vibration_level", "brake_health"]]
y = df["risk_label"]

# Train model
model = RandomForestClassifier(
    n_estimators=150,
    random_state=42
)
model.fit(X, y)

# Save model
with open("maintenance_rf_model.pkl", "wb") as f:
    pickle.dump(model, f)

print("✅ Random Forest Model Trained & Saved")
