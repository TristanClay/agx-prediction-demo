"""No calibrated error probability or scientific validation is inferred here."""


def assess(prediction_status: str, *, gate_status: str, source_type: str) -> dict:
    return {
        "scientific_status": "EXPLORATORY"
        if prediction_status == "SUCCESS"
        else "INVALID_OUT_OF_SCOPE",
        "gate_status": gate_status,
        "calibration_status": "UNCALIBRATED",
        "source_type": source_type,
        "confidence_probability": None,
        "allowed_use": "internal offline exploration only",
    }
