from pathlib import Path

import easyocr


ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "models" / "easyocr"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

easyocr.Reader(
    ["vi", "en"],
    gpu=True,
    model_storage_directory=str(MODEL_DIR),
    user_network_directory=str(MODEL_DIR),
    download_enabled=True,
    verbose=False,
)
print(f"OCR models ready: {MODEL_DIR}")
