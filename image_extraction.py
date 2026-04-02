import os
import cv2
import numpy as np
import tempfile
import uuid
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import subprocess
from PIL import Image
import hashlib
import time

class VideoFrameExtractor:
    """Enhanced video frame extraction using computer vision techniques for AI conference analysis"""
    
    def __init__(self):
        self.temp_dir = tempfile.gettempdir()
        self.similarity_threshold = 0.85  # Threshold for frame similarity
        self.min_time_between_frames = 2.0  # Minimum seconds between extracted frames
        self.max_frames = 50  # Maximum number of frames to extract
        self.quality = 85  # JPEG quality for saved frames
        
        # Enhanced parameters for different content types
        self.presentation_mode = False  # Special mode for presentation videos
        self.meeting_mode = False  # Special mode for meeting recordings
        
    def extract_frames(self, video_path: str, mode: str = "auto") -> List[Dict]:
        """
        Extract significant frames from video with enhanced content analysis
        
        Args:
            video_path: Path to video file
            mode: Extraction mode ("auto", "presentation", "meeting", "uniform")
        
        Returns:
            List of frame information dictionaries
        """
        try:
            if not os.path.exists(video_path):
                print(f"Video file not found: {video_path}")
                return []
            
            # Set mode-specific parameters
            self._configure_extraction_mode(mode)
            
            # Open video
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                print(f"Could not open video: {video_path}")
                return []
            
            # Get video properties
            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            duration = frame_count / fps if fps > 0 else 0
            
            print(f"Processing video: {duration:.1f}s, {fps:.1f} FPS, {frame_count} frames, {width}x{height}")
            
            # Choose extraction method based on mode
            if mode == "uniform":
                extracted_frames = self._extract_uniform_frames(cap, fps, duration)
            elif mode == "presentation":
                extracted_frames = self._extract_presentation_frames(cap, fps)
            elif mode == "meeting":
                extracted_frames = self._extract_meeting_frames(cap, fps)
            else:  # auto mode
                extracted_frames = self._extract_content_frames(cap, fps)
            
            cap.release()
            
            print(f"Extracted {len(extracted_frames)} significant frames from video")
            return extracted_frames
            
        except Exception as e:
            print(f"Error extracting frames: {e}")
            return []
    
    def _configure_extraction_mode(self, mode: str):
        """Configure extraction parameters based on content type"""
        if mode == "presentation":
            self.similarity_threshold = 0.80  # Lower threshold for slide changes
            self.min_time_between_frames = 5.0  # Allow more frequent extraction for slides
            self.max_frames = 100  # More frames for presentations
            self.presentation_mode = True
            
        elif mode == "meeting":
            self.similarity_threshold = 0.90  # Higher threshold for meeting stability
            self.min_time_between_frames = 10.0  # Less frequent for meetings
            self.max_frames = 30  # Fewer frames for meetings
            self.meeting_mode = True
            
        elif mode == "uniform":
            self.min_time_between_frames = None  # Will be calculated
            
        else:  # auto mode
            self.similarity_threshold = 0.85
            self.min_time_between_frames = 2.0
            self.max_frames = 50
    
    def _extract_content_frames(self, cap: cv2.VideoCapture, fps: float) -> List[Dict]:
        """Extract frames based on content similarity analysis with enhanced detection"""
        extracted_frames = []
        prev_frame = None
        prev_frame_time = -self.min_time_between_frames
        frame_number = 0
        
        # Calculate frame skip for efficiency
        skip_frames = max(1, int(fps / 2))  # Process 2 frames per second initially
        
        while len(extracted_frames) < self.max_frames:
            ret, frame = cap.read()
            if not ret:
                break
            
            current_time = frame_number / fps
            
            # Skip frames for performance
            if frame_number % skip_frames != 0:
                frame_number += 1
                continue
            
            # Ensure minimum time between extractions
            if current_time - prev_frame_time < self.min_time_between_frames:
                frame_number += 1
                continue
            
            # Process frame
            try:
                is_significant = self._is_significant_change(frame, prev_frame)
                
                if is_significant or prev_frame is None:
                    # Additional quality checks
                    if self._is_frame_quality_sufficient(frame):
                        # Save frame
                        saved_frame = self._save_frame(frame, current_time, frame_number)
                        if saved_frame:
                            # Add additional metadata
                            saved_frame.update(self._analyze_frame_content(frame))
                            extracted_frames.append(saved_frame)
                            prev_frame_time = current_time
                            
                            # Update previous frame for comparison
                            prev_frame = self._preprocess_frame(frame)
                            print(f"Extracted frame at {current_time:.1f}s (quality score: {saved_frame.get('quality_score', 'unknown')})")
                
            except Exception as e:
                print(f"Error processing frame {frame_number}: {e}")
            
            frame_number += 1
        
        return extracted_frames
    
    def _extract_presentation_frames(self, cap: cv2.VideoCapture, fps: float) -> List[Dict]:
        """Extract frames optimized for presentation content (slides, screen sharing)"""
        extracted_frames = []
        prev_frame = None
        frame_number = 0
        slide_change_threshold = 0.75  # Lower threshold for slide changes
        
        # For presentations, check every 2 seconds minimum
        skip_frames = max(1, int(fps * 2))
        
        while len(extracted_frames) < self.max_frames:
            ret, frame = cap.read()
            if not ret:
                break
            
            current_time = frame_number / fps
            
            if frame_number % skip_frames != 0:
                frame_number += 1
                continue
            
            try:
                # For presentations, focus on structural changes
                is_slide_change = self._detect_slide_change(frame, prev_frame, slide_change_threshold)
                
                if is_slide_change or prev_frame is None:
                    if self._is_presentation_content(frame):
                        saved_frame = self._save_frame(frame, current_time, frame_number)
                        if saved_frame:
                            saved_frame.update({
                                'content_type': 'presentation',
                                'slide_detected': True,
                                'text_density': self._calculate_text_density(frame)
                            })
                            extracted_frames.append(saved_frame)
                            prev_frame = self._preprocess_frame(frame)
                            print(f"Extracted slide at {current_time:.1f}s")
                
            except Exception as e:
                print(f"Error processing presentation frame {frame_number}: {e}")
            
            frame_number += 1
        
        return extracted_frames
    
    def _extract_meeting_frames(self, cap: cv2.VideoCapture, fps: float) -> List[Dict]:
        """Extract frames optimized for meeting content (people, whiteboards)"""
        extracted_frames = []
        prev_frame = None
        frame_number = 0
        
        # For meetings, check every 10 seconds minimum
        skip_frames = max(1, int(fps * 10))
        
        while len(extracted_frames) < self.max_frames:
            ret, frame = cap.read()
            if not ret:
                break
            
            current_time = frame_number / fps
            
            if frame_number % skip_frames != 0:
                frame_number += 1
                continue
            
            try:
                # For meetings, look for scene changes or new speakers
                is_scene_change = self._detect_scene_change(frame, prev_frame)
                
                if is_scene_change or prev_frame is None:
                    if self._is_meeting_content(frame):
                        saved_frame = self._save_frame(frame, current_time, frame_number)
                        if saved_frame:
                            saved_frame.update({
                                'content_type': 'meeting',
                                'scene_change': True,
                                'people_detected': self._detect_people_presence(frame)
                            })
                            extracted_frames.append(saved_frame)
                            prev_frame = self._preprocess_frame(frame)
                            print(f"Extracted meeting scene at {current_time:.1f}s")
                
            except Exception as e:
                print(f"Error processing meeting frame {frame_number}: {e}")
            
            frame_number += 1
        
        return extracted_frames
    
    def _extract_uniform_frames(self, cap: cv2.VideoCapture, fps: float, duration: float) -> List[Dict]:
        """Extract frames at uniform intervals"""
        extracted_frames = []
        
        if duration <= 0:
            return extracted_frames
        
        # Calculate interval to get desired number of frames
        interval = duration / min(self.max_frames, duration / 5)  # At least 5 seconds apart
        current_time = interval / 2  # Start offset
        
        while current_time < duration and len(extracted_frames) < self.max_frames:
            # Seek to specific time
            frame_number = int(current_time * fps)
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
            
            ret, frame = cap.read()
            if ret and self._is_frame_quality_sufficient(frame):
                saved_frame = self._save_frame(frame, current_time, frame_number)
                if saved_frame:
                    saved_frame.update({
                        'content_type': 'uniform',
                        'extraction_method': 'uniform_interval'
                    })
                    extracted_frames.append(saved_frame)
                    print(f"Extracted uniform frame at {current_time:.1f}s")
            
            current_time += interval
        
        return extracted_frames
    
    def _is_significant_change(self, current_frame: np.ndarray, prev_frame: Optional[np.ndarray]) -> bool:
        """Determine if current frame represents a significant change"""
        if prev_frame is None:
            return True
        
        try:
            # Preprocess both frames
            curr_processed = self._preprocess_frame(current_frame)
            
            # Calculate multiple similarity metrics
            structural_sim = self._calculate_structural_similarity(curr_processed, prev_frame)
            histogram_sim = self._calculate_histogram_similarity(curr_processed, prev_frame)
            edge_sim = self._calculate_edge_similarity(curr_processed, prev_frame)
            
            # Weighted combination
            combined_similarity = (
                0.4 * structural_sim +
                0.3 * histogram_sim +
                0.3 * edge_sim
            )
            
            # Frame is significant if similarity is below threshold
            return combined_similarity < self.similarity_threshold
            
        except Exception as e:
            print(f"Error calculating frame similarity: {e}")
            return False
    
    def _detect_slide_change(self, current_frame: np.ndarray, prev_frame: Optional[np.ndarray], threshold: float) -> bool:
        """Detect slide changes in presentation content"""
        if prev_frame is None:
            return True
        
        try:
            curr_processed = self._preprocess_frame(current_frame)
            
            # Focus on edge-based comparison for slides
            edge_similarity = self._calculate_edge_similarity(curr_processed, prev_frame)
            
            # Check for text regions change
            text_similarity = self._calculate_text_region_similarity(curr_processed, prev_frame)
            
            # Combined metric
            slide_similarity = 0.6 * edge_similarity + 0.4 * text_similarity
            
            return slide_similarity < threshold
            
        except Exception as e:
            return False
    
    def _detect_scene_change(self, current_frame: np.ndarray, prev_frame: Optional[np.ndarray]) -> bool:
        """Detect scene changes in meeting content"""
        if prev_frame is None:
            return True
        
        try:
            curr_processed = self._preprocess_frame(current_frame)
            
            # Focus on overall composition changes
            hist_similarity = self._calculate_histogram_similarity(curr_processed, prev_frame)
            
            # Higher threshold for scene changes (less sensitive)
            return hist_similarity < 0.70
            
        except Exception as e:
            return False
    
    def _is_frame_quality_sufficient(self, frame: np.ndarray) -> bool:
        """Check if frame has sufficient quality for extraction"""
        try:
            # Check if frame is too dark
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            mean_brightness = np.mean(gray)
            
            if mean_brightness < 30:  # Too dark
                return False
            
            # Check for blur (using Laplacian variance)
            blur_score = cv2.Laplacian(gray, cv2.CV_64F).var()
            if blur_score < 100:  # Too blurry
                return False
            
            # Check for uniform content (likely error frame)
            if np.std(gray) < 10:  # Too uniform
                return False
            
            return True
            
        except Exception:
            return True  # Default to accepting if check fails
    
    def _is_presentation_content(self, frame: np.ndarray) -> bool:
        """Detect if frame contains presentation-like content"""
        try:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            
            # Check for high contrast (typical of slides)
            hist = cv2.calcHist([gray], [0], None, [256], [0, 256])
            normalized_hist = hist / hist.sum()
            
            # Look for bimodal distribution (text on background)
            peaks = 0
            for i in range(1, 255):
                if normalized_hist[i] > normalized_hist[i-1] and normalized_hist[i] > normalized_hist[i+1]:
                    if normalized_hist[i] > 0.05:  # Significant peak
                        peaks += 1
            
            return peaks >= 2  # Bimodal or multimodal suggests structured content
            
        except Exception:
            return True  # Default to accepting
    
    def _is_meeting_content(self, frame: np.ndarray) -> bool:
        """Detect if frame contains meeting-like content"""
        try:
            # Simple content validation for meetings
            # Could be enhanced with face detection if needed
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            
            # Check for reasonable contrast and detail
            contrast = np.std(gray)
            return contrast > 20  # Has some detail/variation
            
        except Exception:
            return True
    
    def _detect_people_presence(self, frame: np.ndarray) -> bool:
        """Simple detection of people in frame (could be enhanced with face detection)"""
        try:
            # Placeholder for people detection
            # Could implement face detection with OpenCV's haarcascades or DNN
            # For now, return True as placeholder
            return True
        except Exception:
            return False
    
    def _calculate_text_density(self, frame: np.ndarray) -> float:
        """Calculate text density in frame (useful for presentations)"""
        try:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            
            # Use edge detection to find potential text regions
            edges = cv2.Canny(gray, 50, 150)
            text_pixels = np.sum(edges > 0)
            total_pixels = edges.shape[0] * edges.shape[1]
            
            return text_pixels / total_pixels
        except Exception:
            return 0.0
    
    def _analyze_frame_content(self, frame: np.ndarray) -> Dict:
        """Analyze frame content for additional metadata"""
        try:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            
            # Calculate quality metrics
            brightness = np.mean(gray)
            contrast = np.std(gray)
            blur_score = cv2.Laplacian(gray, cv2.CV_64F).var()
            
            # Normalize quality score (0-1)
            quality_score = min(1.0, (blur_score / 1000) * (contrast / 100) * min(brightness / 128, 1))
            
            return {
                'brightness': float(brightness),
                'contrast': float(contrast),
                'blur_score': float(blur_score),
                'quality_score': float(quality_score)
            }
        except Exception:
            return {}
    
    def _preprocess_frame(self, frame: np.ndarray) -> np.ndarray:
        """Preprocess frame for comparison with enhanced normalization"""
        try:
            # Resize to standard size for comparison
            resized = cv2.resize(frame, (320, 240))
            
            # Convert to grayscale
            gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
            
            # Normalize brightness
            normalized = cv2.equalizeHist(gray)
            
            # Apply slight blur to ignore minor pixel changes
            blurred = cv2.GaussianBlur(normalized, (5, 5), 0)
            
            return blurred
            
        except Exception as e:
            print(f"Error preprocessing frame: {e}")
            return frame
    
    def _calculate_structural_similarity(self, frame1: np.ndarray, frame2: np.ndarray) -> float:
        """Calculate structural similarity between frames"""
        try:
            # Use template matching for structural similarity
            if frame1.shape != frame2.shape:
                frame2 = cv2.resize(frame2, (frame1.shape[1], frame1.shape[0]))
            
            result = cv2.matchTemplate(frame1, frame2, cv2.TM_CCOEFF_NORMED)
            return float(np.max(result))
        except Exception:
            # Fallback: normalized cross-correlation
            try:
                frame1_flat = frame1.flatten().astype(np.float64)
                frame2_flat = frame2.flatten().astype(np.float64)
                
                # Normalize
                frame1_norm = (frame1_flat - np.mean(frame1_flat)) / np.std(frame1_flat)
                frame2_norm = (frame2_flat - np.mean(frame2_flat)) / np.std(frame2_flat)
                
                correlation = np.corrcoef(frame1_norm, frame2_norm)[0, 1]
                return float(correlation) if not np.isnan(correlation) else 0.0
            except Exception:
                return 0.0
    
    def _calculate_histogram_similarity(self, frame1: np.ndarray, frame2: np.ndarray) -> float:
        """Calculate histogram similarity between frames"""
        try:
            # Calculate histograms
            hist1 = cv2.calcHist([frame1], [0], None, [256], [0, 256])
            hist2 = cv2.calcHist([frame2], [0], None, [256], [0, 256])
            
            # Compare histograms using correlation method
            correlation = cv2.compareHist(hist1, hist2, cv2.HISTCMP_CORREL)
            return float(correlation) if not np.isnan(correlation) else 0.0
            
        except Exception:
            return 0.0
    
    def _calculate_edge_similarity(self, frame1: np.ndarray, frame2: np.ndarray) -> float:
        """Calculate edge similarity between frames"""
        try:
            # Apply Canny edge detection
            edges1 = cv2.Canny(frame1, 50, 150)
            edges2 = cv2.Canny(frame2, 50, 150)
            
            # Calculate similarity of edge maps
            diff = cv2.absdiff(edges1, edges2)
            similarity = 1.0 - (np.sum(diff) / (diff.shape[0] * diff.shape[1] * 255))
            
            return float(similarity)
            
        except Exception:
            return 0.0
    
    def _calculate_text_region_similarity(self, frame1: np.ndarray, frame2: np.ndarray) -> float:
        """Calculate similarity of text regions (useful for presentation analysis)"""
        try:
            # Use MSER to detect text-like regions
            mser = cv2.MSER_create()
            
            regions1, _ = mser.detectRegions(frame1)
            regions2, _ = mser.detectRegions(frame2)
            
            # Simple comparison based on number of regions
            if len(regions1) == 0 and len(regions2) == 0:
                return 1.0
            
            region_ratio = min(len(regions1), len(regions2)) / max(len(regions1), len(regions2), 1)
            return float(region_ratio)
            
        except Exception:
            return 1.0  # Default to similar if detection fails
    
    def _save_frame(self, frame: np.ndarray, timestamp: float, frame_number: int) -> Optional[Dict]:
        """Save extracted frame to temporary file with enhanced metadata"""
        try:
            # Generate unique filename
            frame_id = str(uuid.uuid4())
            filename = f"frame_{frame_id}_{int(timestamp)}s.jpg"
            filepath = os.path.join(self.temp_dir, filename)
            
            # Save frame as JPEG with specified quality
            success = cv2.imwrite(filepath, frame, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
            
            if success and os.path.exists(filepath):
                # Get file size and dimensions
                file_size = os.path.getsize(filepath)
                height, width = frame.shape[:2]
                
                return {
                    'filename': filename,
                    'path': filepath,
                    'timestamp': timestamp,
                    'frame_number': frame_number,
                    'file_size': file_size,
                    'width': width,
                    'height': height,
                    'created_at': datetime.now().isoformat(),
                    'quality': self.quality
                }
            else:
                print(f"Failed to save frame at {timestamp}s")
                return None
                
        except Exception as e:
            print(f"Error saving frame: {e}")
            return None
    
    def extract_frames_at_intervals(self, video_path: str, interval_seconds: float = 30.0) -> List[Dict]:
        """Extract frames at regular intervals (fallback method)"""
        try:
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                return []
            
            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            duration = frame_count / fps if fps > 0 else 0
            
            extracted_frames = []
            current_time = 0.0
            
            while current_time < duration and len(extracted_frames) < self.max_frames:
                # Seek to specific time
                frame_number = int(current_time * fps)
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
                
                ret, frame = cap.read()
                if ret and self._is_frame_quality_sufficient(frame):
                    saved_frame = self._save_frame(frame, current_time, frame_number)
                    if saved_frame:
                        saved_frame.update({
                            'extraction_method': 'interval',
                            'interval': interval_seconds
                        })
                        extracted_frames.append(saved_frame)
                
                current_time += interval_seconds
            
            cap.release()
            return extracted_frames
            
        except Exception as e:
            print(f"Error extracting frames at intervals: {e}")
            return []
    
    def get_frame_hash(self, frame: np.ndarray) -> str:
        """Generate hash for frame comparison"""
        try:
            # Resize and convert to grayscale for consistent hashing
            small_frame = cv2.resize(frame, (16, 16))
            gray_frame = cv2.cvtColor(small_frame, cv2.COLOR_BGR2GRAY)
            
            # Create hash from pixel values
            frame_hash = hashlib.md5(gray_frame.tobytes()).hexdigest()
            return frame_hash
            
        except Exception as e:
            print(f"Error generating frame hash: {e}")
            return ""
    
    def cleanup_temp_files(self, frame_list: List[Dict]):
        """Clean up temporary frame files"""
        for frame_info in frame_list:
            try:
                if 'path' in frame_info and os.path.exists(frame_info['path']):
                    os.remove(frame_info['path'])
                    print(f"Cleaned up frame file: {frame_info['filename']}")
            except Exception as e:
                print(f"Error cleaning up frame file: {e}")
    
    def get_video_info(self, video_path: str) -> Dict:
        """Get comprehensive video information"""
        try:
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                return {'error': 'Could not open video'}
            
            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            duration = frame_count / fps if fps > 0 else 0
            
            cap.release()
            
            # Get file size
            file_size = os.path.getsize(video_path) if os.path.exists(video_path) else 0
            
            return {
                'duration': duration,
                'fps': fps,
                'frame_count': frame_count,
                'resolution': f"{width}x{height}",
                'width': width,
                'height': height,
                'file_size': file_size,
                'file_size_mb': file_size / (1024 * 1024),
                'aspect_ratio': width / height if height > 0 else 0,
                'estimated_quality': self._estimate_video_quality(width, height, fps)
            }
            
        except Exception as e:
            return {'error': str(e)}
    
    def _estimate_video_quality(self, width: int, height: int, fps: float) -> str:
        """Estimate video quality based on resolution and frame rate"""
        pixel_count = width * height
        
        if pixel_count >= 1920 * 1080 and fps >= 24:
            return 'high'
        elif pixel_count >= 1280 * 720 and fps >= 15:
            return 'medium'
        else:
            return 'low'

class ImageAnalyzer:
    """Enhanced image analysis utilities for conference content"""
    
    def __init__(self):
        pass
    
    def detect_slide_content(self, image_path: str) -> Dict:
        """Enhanced slide content detection"""
        try:
            image = cv2.imread(image_path)
            if image is None:
                return {'error': 'Could not load image'}
            
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            
            # Detect text regions using multiple methods
            text_regions = self._detect_text_regions_advanced(gray)
            
            # Detect geometric shapes and structures
            shapes = self._detect_presentation_elements(gray)
            
            # Calculate various metrics
            text_density = len(text_regions) / (gray.shape[0] * gray.shape[1]) * 1000
            edge_density = self._calculate_edge_density(gray)
            contrast_ratio = self._calculate_contrast_ratio(gray)
            
            # Determine if it's likely presentation content
            is_presentation = (
                text_density > 0.5 or 
                len(shapes) > 3 or
                contrast_ratio > 2.0
            )
            
            return {
                'text_regions': len(text_regions),
                'shapes_detected': len(shapes),
                'text_density': text_density,
                'edge_density': edge_density,
                'contrast_ratio': contrast_ratio,
                'likely_slide': is_presentation,
                'confidence': self._calculate_slide_confidence(text_density, len(shapes), contrast_ratio)
            }
            
        except Exception as e:
            return {'error': str(e)}
    
    def _detect_text_regions_advanced(self, gray_image: np.ndarray) -> List:
        """Advanced text region detection using multiple methods"""
        try:
            regions = []
            
            # Method 1: MSER (Maximally Stable Extremal Regions)
            try:
                mser = cv2.MSER_create()
                mser_regions, _ = mser.detectRegions(gray_image)
                regions.extend(mser_regions)
            except Exception:
                pass
            
            # Method 2: Contour-based text detection
            try:
                # Apply morphological operations to connect text components
                kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
                morph = cv2.morphologyEx(gray_image, cv2.MORPH_CLOSE, kernel)
                
                # Find contours that could be text
                contours, _ = cv2.findContours(morph, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                
                text_contours = []
                for contour in contours:
                    area = cv2.contourArea(contour)
                    if 100 < area < 10000:  # Filter by reasonable text size
                        x, y, w, h = cv2.boundingRect(contour)
                        aspect_ratio = w / h
                        if 0.1 < aspect_ratio < 10:  # Reasonable aspect ratio for text
                            text_contours.append(contour)
                
                regions.extend(text_contours)
            except Exception:
                pass
            
            return regions
        except Exception:
            return []
    
    def _detect_presentation_elements(self, gray_image: np.ndarray) -> List:
        """Detect geometric shapes and presentation elements"""
        try:
            shapes = []
            
            # Find contours
            edges = cv2.Canny(gray_image, 50, 150)
            contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            for contour in contours:
                area = cv2.contourArea(contour)
                if area > 500:  # Filter small contours
                    # Approximate contour to polygon
                    epsilon = 0.02 * cv2.arcLength(contour, True)
                    approx = cv2.approxPolyDP(contour, epsilon, True)
                    
                    # Classify shape based on number of vertices
                    vertices = len(approx)
                    if 3 <= vertices <= 8:  # Reasonable polygon
                        shapes.append({
                            'type': f'{vertices}-sided polygon',
                            'area': area,
                            'vertices': vertices
                        })
            
            return shapes
        except Exception:
            return []
    
    def _calculate_edge_density(self, gray_image: np.ndarray) -> float:
        """Calculate density of edges in image"""
        try:
            edges = cv2.Canny(gray_image, 50, 150)
            edge_pixels = np.sum(edges > 0)
            total_pixels = edges.shape[0] * edges.shape[1]
            return edge_pixels / total_pixels
        except Exception:
            return 0.0
    
    def _calculate_contrast_ratio(self, gray_image: np.ndarray) -> float:
        """Calculate contrast ratio in image"""
        try:
            # Calculate histogram
            hist = cv2.calcHist([gray_image], [0], None, [256], [0, 256])
            
            # Find peaks (modes) in histogram
            peaks = []
            for i in range(1, 255):
                if hist[i] > hist[i-1] and hist[i] > hist[i+1]:
                    if hist[i] > 0.01 * np.sum(hist):  # Significant peak
                        peaks.append(i)
            
            if len(peaks) >= 2:
                # Calculate ratio between highest and lowest peaks
                return max(peaks) / max(min(peaks), 1)
            else:
                # Use standard deviation as contrast measure
                return float(np.std(gray_image) / 64)
        except Exception:
            return 1.0
    
    def _calculate_slide_confidence(self, text_density: float, shape_count: int, contrast_ratio: float) -> float:
        """Calculate confidence that image is a slide"""
        try:
            # Weighted scoring
            text_score = min(text_density / 2.0, 1.0) * 0.4
            shape_score = min(shape_count / 10.0, 1.0) * 0.3
            contrast_score = min(contrast_ratio / 3.0, 1.0) * 0.3
            
            return text_score + shape_score + contrast_score
        except Exception:
            return 0.0