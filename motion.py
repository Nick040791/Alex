import time
import cv2
import numpy as np
from typing import Tuple, List, Dict, Any, Optional

class MotionDetector:
    def __init__(
        self,
        sensitivity: int = 60,         # 1 - 100 scale (higher = more sensitive)
        min_area: int = 1200,          # Minimum contour area in pixels
        cooldown: float = 6.0,         # Seconds between distinct recorded events
        zone: str = "all",             # 'all', 'bottom', 'center'
        blur_size: int = 21,
    ):
        self.sensitivity = max(1, min(100, sensitivity))
        self.min_area = max(100, min_area)
        self.cooldown = max(1.0, cooldown)
        self.zone = zone
        self.blur_size = blur_size if blur_size % 2 == 1 else blur_size + 1
        
        self.bg_frame: Optional[np.ndarray] = None
        self.last_event_time = 0.0
        self.is_motion_active = False
        self.motion_start_time = 0.0
        self.motion_score = 0.0
        self.enabled = True
        
        # Recalculate derived thresholds
        self._update_thresholds()

    def _update_thresholds(self):
        """
        Derive pixel difference threshold, trigger percentage, and learning rate
        from the 1-100 sensitivity value.
        """
        # Pixel delta threshold: 10 (very sensitive) to 45 (hard to trigger)
        self.pixel_diff_thresh = int(max(8, 48 - (self.sensitivity * 0.40)))
        
        # Trigger percentage of active zone: 0.15% to 5.0%
        # Sensitivity 90 -> 0.4%
        # Sensitivity 50 -> 2.0%
        # Sensitivity 20 -> 3.2%
        self.trigger_threshold = max(0.15, round((100 - self.sensitivity) * 0.045, 2))
        
        # Minimum contour size scale
        self.effective_min_area = max(150, int(self.min_area * (1.2 - (self.sensitivity / 100.0))))

    def update_config(
        self,
        sensitivity: Optional[int] = None,
        min_area: Optional[int] = None,
        cooldown: Optional[float] = None,
        zone: Optional[str] = None,
        enabled: Optional[bool] = None
    ):
        if sensitivity is not None:
            self.sensitivity = max(1, min(100, sensitivity))
        if min_area is not None:
            self.min_area = max(100, min_area)
        if cooldown is not None:
            self.cooldown = max(1.0, cooldown)
        if zone is not None:
            self.zone = zone
        if enabled is not None:
            self.enabled = enabled
        
        self._update_thresholds()

    def reset_background(self):
        """Reset background model to adapt immediately to new camera angle or lighting"""
        self.bg_frame = None

    def _apply_zone_mask(self, mask: np.ndarray, h: int, w: int) -> np.ndarray:
        """Zero out regions outside the selected detection zone"""
        if self.zone == "bottom":
            # Only detect in bottom 60% of frame (doorstep, driveway)
            cutoff = int(h * 0.4)
            mask[:cutoff, :] = 0
        elif self.zone == "center":
            # Center 60% horizontal and 60% vertical
            y1, y2 = int(h * 0.2), int(h * 0.8)
            x1, x2 = int(w * 0.2), int(w * 0.8)
            masked = np.zeros_like(mask)
            masked[y1:y2, x1:x2] = mask[y1:y2, x1:x2]
            return masked
        return mask

    def process_frame(
        self,
        frame: np.ndarray,
        draw_boxes: bool = True
    ) -> Tuple[bool, bool, np.ndarray, float, float, List[Dict[str, int]]]:
        """
        Process a video frame for motion.
        
        Returns:
            - motion_detected (bool): True if motion score >= trigger_threshold
            - is_new_event (bool): True if this frame triggers a new event (respecting cooldown)
            - annotated_frame (np.ndarray): Frame with bounding boxes & zone overlay
            - motion_score (float): Percentage of movement in active zone (0.0 to 100.0)
            - trigger_threshold (float): Current threshold percentage
            - boxes (list of dicts): [{'x': x, 'y': y, 'w': w, 'h': h}, ...]
        """
        if not self.enabled or frame is None or frame.size == 0:
            return False, False, frame, 0.0, self.trigger_threshold, []

        h, w = frame.shape[:2]
        
        # Downscale for fast, robust computer vision
        process_width = 480
        scale = process_width / float(w)
        process_height = int(h * scale)
        small_frame = cv2.resize(frame, (process_width, process_height), interpolation=cv2.INTER_AREA)

        # Convert to Grayscale & Gaussian blur
        gray = cv2.cvtColor(small_frame, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (self.blur_size, self.blur_size), 0)

        # Initialize background model if needed
        if self.bg_frame is None or self.bg_frame.shape != blurred.shape:
            self.bg_frame = blurred.astype(np.float32)
            return False, False, frame, 0.0, self.trigger_threshold, []

        # Running average adaptation (alpha adapts smoothly to slow lighting changes)
        alpha = 0.04
        cv2.accumulateWeighted(blurred, self.bg_frame, alpha)
        bg_converted = cv2.convertScaleAbs(self.bg_frame)

        # Absolute frame difference
        frame_diff = cv2.absdiff(blurred, bg_converted)

        # Threshold using dynamically calibrated pixel_diff_thresh
        _, thresh = cv2.threshold(frame_diff, self.pixel_diff_thresh, 255, cv2.THRESH_BINARY)

        # Apply detection zone mask
        thresh = self._apply_zone_mask(thresh, process_height, process_width)

        # Morphological dilation to group nearby pixels
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        dilated = cv2.dilate(thresh, kernel, iterations=2)

        # Calculate motion score: non-zero pixels as percentage of frame/zone
        motion_pixel_count = cv2.countNonZero(dilated)
        total_pixels = process_width * process_height
        if self.zone == "bottom":
            total_pixels = int(total_pixels * 0.6)
        elif self.zone == "center":
            total_pixels = int(total_pixels * 0.36)

        raw_score = (motion_pixel_count / float(total_pixels)) * 100.0
        self.motion_score = round(min(100.0, raw_score), 2)

        # Find contours for bounding box visualization
        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        annotated = frame.copy() if draw_boxes else frame
        detected_boxes: List[Dict[str, int]] = []
        scaled_min_area = self.effective_min_area * (scale ** 2)

        for c in contours:
            area = cv2.contourArea(c)
            if area > scaled_min_area:
                (sx, sy, sw, sh) = cv2.boundingRect(c)
                ox = int(sx / scale)
                oy = int(sy / scale)
                ow = int(sw / scale)
                oh = int(sh / scale)
                detected_boxes.append({"x": ox, "y": oy, "w": ow, "h": oh})

                if draw_boxes:
                    # Ring cyan bounding boxes
                    color = (255, 168, 0)
                    cv2.rectangle(annotated, (ox, oy), (ox + ow, oy + oh), color, 2)
                    cv2.putText(
                        annotated,
                        f"Motion {self.motion_score}%",
                        (ox, max(22, oy - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.55,
                        color,
                        2
                    )

        # Zone border visual (subtle dashed/dotted guidelines if not 'all')
        if draw_boxes and self.zone != "all":
            zone_color = (120, 100, 40)
            if self.zone == "bottom":
                zy = int(h * 0.4)
                cv2.line(annotated, (0, zy), (w, zy), zone_color, 1)
                cv2.putText(annotated, "[Active Motion Zone: Lower 60%]", (16, zy - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, zone_color, 1)
            elif self.zone == "center":
                y1, y2 = int(h * 0.2), int(h * 0.8)
                x1, x2 = int(w * 0.2), int(w * 0.8)
                cv2.rectangle(annotated, (x1, y1), (x2, y2), zone_color, 1)
                cv2.putText(annotated, "[Active Motion Zone: Center]", (x1 + 8, y1 - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, zone_color, 1)

        # Motion is officially detected if motion_score >= trigger_threshold OR significant boxes exist
        motion_detected = (self.motion_score >= self.trigger_threshold) and (len(detected_boxes) > 0 or self.motion_score > 1.0)

        now = time.time()
        is_new_event = False

        if motion_detected:
            if not self.is_motion_active:
                self.is_motion_active = True
                self.motion_start_time = now

            if (now - self.last_event_time) > self.cooldown:
                self.last_event_time = now
                is_new_event = True
        else:
            self.is_motion_active = False

        return motion_detected, is_new_event, annotated, self.motion_score, self.trigger_threshold, detected_boxes
