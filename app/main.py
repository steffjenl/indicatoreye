import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np
import requests
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

CONFIG_PATH = Path(os.getenv("CONFIG_PATH", "/config/config.json"))

app = FastAPI(title="IndicatorEye", version="1.0.0")


class LampConfig(BaseModel):
    name: str
    x: int
    y: int
    radius: int = 10
    expected_color: str = Field(default="auto", description="green, orange, red, white, auto")
    min_brightness: float = 70.0
    min_saturation: float = 45.0
    min_color_score: float = 0.08


class ServiceConfig(BaseModel):
    snapshot_url: Optional[str] = None
    timeout_seconds: int = 10
    tls_verify: bool = False
    jpeg_quality: int = 85
    lamps: List[LampConfig]


def load_config() -> ServiceConfig:
    if not CONFIG_PATH.exists():
        raise HTTPException(
            status_code=500,
            detail=(
                f"Config file not found at {CONFIG_PATH}. "
                "Copy config/config.example.json to config/config.json first."
            ),
        )
    return ServiceConfig.model_validate_json(CONFIG_PATH.read_text(encoding="utf-8"))


def read_image_from_url(url: str, timeout_seconds: int, tls_verify: bool) -> np.ndarray:
    try:
        response = requests.get(url, timeout=timeout_seconds, verify=tls_verify)
        response.raise_for_status()
    except requests.exceptions.SSLError as exc:
        raise HTTPException(
            status_code=502,
            detail="TLS certificate validation failed while fetching snapshot. Use tls_verify=false for self-signed certificates.",
        ) from exc
    except requests.exceptions.Timeout as exc:
        raise HTTPException(status_code=504, detail="Snapshot request timed out") from exc
    except requests.exceptions.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Snapshot request failed: {exc}") from exc

    image_array = np.asarray(bytearray(response.content), dtype=np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=422, detail="Snapshot could not be decoded as an image")
    return image


def read_image_from_upload(data: bytes) -> np.ndarray:
    image_array = np.asarray(bytearray(data), dtype=np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=422, detail="Upload could not be decoded as an image")
    return image


def circular_crop(image: np.ndarray, lamp: LampConfig) -> np.ndarray:
    height, width = image.shape[:2]
    x1 = max(lamp.x - lamp.radius, 0)
    y1 = max(lamp.y - lamp.radius, 0)
    x2 = min(lamp.x + lamp.radius + 1, width)
    y2 = min(lamp.y + lamp.radius + 1, height)
    crop = image[y1:y2, x1:x2]
    if crop.size == 0:
        raise HTTPException(status_code=422, detail=f"Lamp '{lamp.name}' crop is outside the image")
    return crop


def color_mask(hsv: np.ndarray, expected_color: str) -> np.ndarray:
    # OpenCV HSV hue range is 0..179.
    h = hsv[:, :, 0]
    s = hsv[:, :, 1]
    v = hsv[:, :, 2]
    bright_and_saturated = (s >= 45) & (v >= 60)

    if expected_color == "green":
        mask = (h >= 35) & (h <= 95) & bright_and_saturated
    elif expected_color == "orange":
        mask = (h >= 8) & (h <= 34) & bright_and_saturated
    elif expected_color == "red":
        mask = (((h >= 0) & (h <= 7)) | ((h >= 165) & (h <= 179))) & bright_and_saturated
    elif expected_color == "white":
        mask = (s <= 70) & (v >= 120)
    else:
        mask = bright_and_saturated
    return mask


def analyze_lamp(image: np.ndarray, lamp: LampConfig) -> Dict[str, Any]:
    crop = circular_crop(image, lamp)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

    brightness = float(np.mean(gray))
    saturation = float(np.mean(hsv[:, :, 1]))
    mask = color_mask(hsv, lamp.expected_color)
    color_score = float(np.count_nonzero(mask) / mask.size)

    is_on = (
        brightness >= lamp.min_brightness
        and saturation >= lamp.min_saturation
        and color_score >= lamp.min_color_score
    )

    return {
        "name": lamp.name,
        "state": "on" if is_on else "off",
        "on": is_on,
        "x": lamp.x,
        "y": lamp.y,
        "radius": lamp.radius,
        "expected_color": lamp.expected_color,
        "brightness": round(brightness, 2),
        "saturation": round(saturation, 2),
        "color_score": round(color_score, 4),
        "thresholds": {
            "min_brightness": lamp.min_brightness,
            "min_saturation": lamp.min_saturation,
            "min_color_score": lamp.min_color_score,
        },
    }


def analyze_image(image: np.ndarray, config: ServiceConfig) -> Dict[str, Any]:
    lamps = [analyze_lamp(image, lamp) for lamp in config.lamps]
    return {
        "ok": True,
        "checked_at": int(time.time()),
        "image": {
            "width": int(image.shape[1]),
            "height": int(image.shape[0]),
        },
        "lamps": lamps,
    }


def filter_lamps(result: Dict[str, Any], lamp_name: Optional[str]) -> Dict[str, Any]:
    if lamp_name is None:
        return result

    filtered_lamps = [lamp for lamp in result["lamps"] if lamp["name"] == lamp_name]
    if not filtered_lamps:
        raise HTTPException(status_code=404, detail=f"Lamp '{lamp_name}' not found")

    filtered_result = dict(result)
    filtered_result["lamps"] = filtered_lamps
    return filtered_result


def to_homey_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "success": True,
        "checked_at": result["checked_at"],
        "devices": [
            {
                "name": lamp["name"],
                "on": lamp["on"],
            }
            for lamp in result["lamps"]
        ],
    }


@app.get("/config")
def get_config() -> Dict[str, Any]:
    return load_config().model_dump()


@app.get("/status")
def status(
    lamp: Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    config = load_config()
    url = config.snapshot_url
    if not url:
        raise HTTPException(status_code=400, detail="No snapshot_url configured")

    image = read_image_from_url(url, config.timeout_seconds, config.tls_verify)
    result = analyze_image(image, config)
    result["request"] = {
        "snapshot_url": url,
        "timeout_seconds": config.timeout_seconds,
        "tls_verify": config.tls_verify,
    }
    return filter_lamps(result, lamp)


@app.get("/homey")
def homey(lamp: Optional[str] = Query(default=None)) -> Dict[str, Any]:
    config = load_config()
    url = config.snapshot_url
    if not url:
        raise HTTPException(status_code=400, detail="No snapshot_url configured")

    image = read_image_from_url(url, config.timeout_seconds, config.tls_verify)
    result = analyze_image(image, config)
    filtered_result = filter_lamps(result, lamp)
    return to_homey_payload(filtered_result)


@app.post("/analyze")
async def analyze(file: UploadFile = File(...)) -> Dict[str, Any]:
    config = load_config()
    data = await file.read()
    image = read_image_from_upload(data)
    return analyze_image(image, config)
