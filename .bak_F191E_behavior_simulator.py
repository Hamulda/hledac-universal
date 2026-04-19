"""
Behavior Simulator for Stealth Web Research

Simulates human-like browsing behavior to avoid bot detection:
- Randomized mouse movements (Bézier curves)
- Natural scroll patterns
- Variable timing between actions
- Randomized viewport interactions
- Human-like typing patterns

M1-Optimized: Minimal CPU usage, efficient randomization
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class BehaviorPattern(Enum):
    """Pre-defined behavior patterns"""
    CASUAL = "casual"  # Slow, relaxed browsing
    RESEARCHER = "researcher"  # Focused, methodical
    QUICK = "quick"  # Fast but human-like
    CAREFUL = "careful"  # Very slow, cautious


@dataclass
class SimulationConfig:
    """Configuration for behavior simulation"""
    pattern: BehaviorPattern = BehaviorPattern.RESEARCHER
    
    # Timing (in seconds)
    min_delay: float = 0.5
    max_delay: float = 3.0
    
    # Mouse movement
    mouse_speed: float = 1.0  # Multiplier
    
    # Scrolling
    scroll_min: int = 100  # pixels
    scroll_max: int = 800
    scroll_pause: float = 0.1
    
    # Randomization
    randomness: float = 0.3  # 0-1, higher = more random
    
    # Viewport
    viewport_variation: bool = True  # Vary viewport slightly


@dataclass
class MouseMovement:
    """Mouse movement point"""
    x: float
    y: float
    timestamp: float


@dataclass
class ScrollAction:
    """Scroll action"""
    delta_y: int
    duration: float
    pause_after: float


class BehaviorSimulator:
    """
    Simulate human-like web browsing behavior.
    
    Example:
        >>> simulator = BehaviorSimulator()
        >>> await simulator.simulate_reading(duration=30)
        >>> await simulator.simulate_scroll(direction='down')
        >>> await simulator.simulate_click(x=100, y=200)
    """
    
    # Pattern presets
    PATTERNS: Dict[BehaviorPattern, Dict[str, Any]] = {
        BehaviorPattern.CASUAL: {
            'min_delay': 1.0,
            'max_delay': 5.0,
            'mouse_speed': 0.7,
            'scroll_min': 200,
            'scroll_max': 1000,
            'scroll_pause': 0.2,
            'randomness': 0.4,
        },
        BehaviorPattern.RESEARCHER: {
            'min_delay': 0.8,
            'max_delay': 2.5,
            'mouse_speed': 1.0,
            'scroll_min': 300,
            'scroll_max': 800,
            'scroll_pause': 0.15,
            'randomness': 0.25,
        },
        BehaviorPattern.QUICK: {
            'min_delay': 0.3,
            'max_delay': 1.2,
            'mouse_speed': 1.3,
            'scroll_min': 400,
            'scroll_max': 1200,
            'scroll_pause': 0.05,
            'randomness': 0.35,
        },
        BehaviorPattern.CAREFUL: {
            'min_delay': 2.0,
            'max_delay': 8.0,
            'mouse_speed': 0.5,
            'scroll_min': 100,
            'scroll_max': 400,
            'scroll_pause': 0.3,
            'randomness': 0.2,
        },
    }
    
    def __init__(self, config: Optional[SimulationConfig] = None):
        self.config = config or SimulationConfig()
        self._apply_pattern()
        
        # State tracking
        self.last_action_time: float = time.time()
        self.mouse_position: Tuple[int, int] = (0, 0)
        self.scroll_position: int = 0
        self.action_count: int = 0
        
        # Viewport
        self.viewport_width: int = 1920
        self.viewport_height: int = 1080
    
    def _apply_pattern(self):
        """Apply pattern preset to config"""
        if self.config.pattern in self.PATTERNS:
            preset = self.PATTERNS[self.config.pattern]
            for key, value in preset.items():
                setattr(self.config, key, value)
    
    def _random_delay(self, min_mult: float = 0.8, max_mult: float = 1.2) -> float:
        """Generate random delay with variation"""
        base = random.uniform(self.config.min_delay, self.config.max_delay)
        variation = random.uniform(min_mult, max_mult)
        return base * variation
    
    def _apply_randomness(self, value: float) -> float:
        """Apply randomness factor to value"""
        if self.config.randomness <= 0:
            return value
        
        variation = value * self.config.randomness
        return value + random.uniform(-variation, variation)
    
    def _bezier_curve(
        self,
        p0: Tuple[float, float],
        p1: Tuple[float, float],
        p2: Tuple[float, float],
        t: float
    ) -> Tuple[float, float]:
        """Calculate quadratic Bézier curve point"""
        x = (1-t)**2 * p0[0] + 2*(1-t)*t * p1[0] + t**2 * p2[0]
        y = (1-t)**2 * p0[1] + 2*(1-t)*t * p1[1] + t**2 * p2[1]
        return (x, y)
    
    def generate_mouse_path(
        self,
        start: Tuple[int, int],
        end: Tuple[int, int],
        num_points: int = 20
    ) -> List[MouseMovement]:
        """
        Generate human-like mouse path using Bézier curve.
        
        Args:
            start: Starting position (x, y)
            end: Ending position (x, y)
            num_points: Number of points in path
            
        Returns:
            List of mouse movement points
        """
        # Calculate control point for curve (add some randomness)
        mid_x = (start[0] + end[0]) / 2
        mid_y = (start[1] + end[1]) / 2
        
        # Add random offset to control point
        offset_range = abs(end[0] - start[0]) + abs(end[1] - start[1])
        offset_range *= 0.2 * self.config.randomness
        
        control = (
            mid_x + random.uniform(-offset_range, offset_range),
            mid_y + random.uniform(-offset_range, offset_range)
        )
        
        # Generate points along curve
        points = []
        now = time.time()
        
        for i in range(num_points):
            t = i / (num_points - 1)
            x, y = self._bezier_curve(start, control, end, t)
            
            # Add slight jitter
            jitter = self.config.randomness * 2
            x += random.uniform(-jitter, jitter)
            y += random.uniform(-jitter, jitter)
            
            # Calculate timestamp (movement speed varies)
            speed_variation = random.uniform(0.8, 1.2) / self.config.mouse_speed
            timestamp = now + (i * 0.01 * speed_variation)
            
            points.append(MouseMovement(x=x, y=y, timestamp=timestamp))
        
        return points
    
    async def simulate_mouse_move(
        self,
        target_x: int,
        target_y: int,
        callback: Optional[Any] = None
    ) -> None:
        """
        Simulate mouse movement to target position.
        
        Args:
            target_x: Target X coordinate
            target_y: Target Y coordinate
            callback: Optional callback function for each point
        """
        path = self.generate_mouse_path(
            self.mouse_position,
            (target_x, target_y)
        )
        
        for point in path:
            self.mouse_position = (int(point.x), int(point.y))
            
            if callback:
                await callback(self.mouse_position)
            
            # Small delay between movements
            await asyncio.sleep(0.005)
        
        self.action_count += 1
        self.last_action_time = time.time()
    
    async def simulate_click(
        self,
        x: Optional[int] = None,
        y: Optional[int] = None,
        callback: Optional[Any] = None
    ) -> None:
        """
        Simulate mouse click.
        
        Args:
            x: Click X coordinate (default: current)
            y: Click Y coordinate (default: current)
            callback: Optional callback function
        """
        if x is not None and y is not None:
            await self.simulate_mouse_move(x, y, callback)
        
        # Random delay before click (human reaction time)
        await asyncio.sleep(self._random_delay(0.1, 0.3))
        
        # Simulate click
        if callback:
            await callback(('click', self.mouse_position))
        
        logger.debug(f"Simulated click at {self.mouse_position}")
        
        # Delay after click
        await asyncio.sleep(self._random_delay(0.2, 0.5))
        
        self.action_count += 1
        self.last_action_time = time.time()
    
    async def simulate_scroll(
        self,
        direction: str = 'down',
        amount: Optional[int] = None,
        callback: Optional[Any] = None
    ) -> None:
        """
        Simulate scrolling.
        
        Args:
            direction: 'up' or 'down'
            amount: Scroll amount in pixels (default: random)
            callback: Optional callback function
        """
        if amount is None:
            amount = random.randint(self.config.scroll_min, self.config.scroll_max)
        
        if direction == 'up':
            amount = -amount
        
        # Break into smaller chunks for realism
        chunk_size = 100
        remaining = amount
        
        while abs(remaining) > 0:
            chunk = min(chunk_size, abs(remaining))
            if remaining < 0:
                chunk = -chunk
            
            if callback:
                await callback(('scroll', chunk))
            
            self.scroll_position += chunk
            remaining -= chunk
            
            # Pause between scroll chunks
            await asyncio.sleep(
                self._apply_randomness(self.config.scroll_pause)
            )
        
        logger.debug(f"Simulated scroll {amount}px (total: {self.scroll_position})")
        
        self.action_count += 1
        self.last_action_time = time.time()
    
    async def simulate_typing(
        self,
        text: str,
        callback: Optional[Any] = None,
        wpm: int = 60
    ) -> None:
        """
        Simulate human-like typing.
        
        Args:
            text: Text to type
            callback: Optional callback function
            wpm: Words per minute (typing speed)
        """
        # Calculate base delay per character
        chars_per_minute = wpm * 5  # Average word length
        base_delay = 60 / chars_per_minute
        
        for char in text:
            # Add variation to typing speed
            delay = base_delay * random.uniform(0.7, 1.3)
            
            if callback:
                await callback(('type', char))
            
            await asyncio.sleep(delay)
            
            # Occasional longer pause (thinking)
            if random.random() < 0.05:
                await asyncio.sleep(random.uniform(0.2, 0.5))
        
        logger.debug(f"Simulated typing {len(text)} characters")
        
        self.action_count += 1
        self.last_action_time = time.time()
    
    async def simulate_reading(
        self,
        duration: float = 10.0,
        scroll_probability: float = 0.3
    ) -> None:
        """
        Simulate reading a page (idle time with occasional scrolls).
        
        Args:
            duration: Reading duration in seconds
            scroll_probability: Probability of scrolling during reading
        """
        start_time = time.time()
        
        while time.time() - start_time < duration:
            # Random delay
            await asyncio.sleep(self._random_delay(0.5, 1.5))
            
            # Maybe scroll
            if random.random() < scroll_probability:
                direction = 'down' if random.random() > 0.3 else 'up'
                await self.simulate_scroll(direction)
        
        logger.debug(f"Simulated reading for {duration}s")
    
    async def simulate_page_visit(
        self,
        num_scrolls: int = 3,
        read_time: float = 15.0
    ) -> Dict[str, Any]:
        """
        Simulate complete page visit behavior.
        
        Args:
            num_scrolls: Number of scroll actions
            read_time: Time spent reading
            
        Returns:
            Statistics about the simulated visit
        """
        start_time = time.time()
        
        # Initial pause (page loading)
        await asyncio.sleep(self._random_delay(0.5, 1.5))
        
        # Reading
        await self.simulate_reading(
            duration=read_time,
            scroll_probability=0.4
        )
        
        # Additional scrolls
        for _ in range(num_scrolls):
            if random.random() > 0.3:  # 70% chance to scroll
                direction = random.choice(['up', 'down'])
                await self.simulate_scroll(direction)
                
                # Short read after scroll
                await asyncio.sleep(self._random_delay(1.0, 3.0))
        
        duration = time.time() - start_time
        
        return {
            'duration': duration,
            'actions': self.action_count,
            'scroll_position': self.scroll_position,
            'pattern': self.config.pattern.value,
        }
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get simulation statistics"""
        return {
            'action_count': self.action_count,
            'mouse_position': self.mouse_position,
            'scroll_position': self.scroll_position,
            'last_action_time': self.last_action_time,
            'pattern': self.config.pattern.value,
            'config': {
                'min_delay': self.config.min_delay,
                'max_delay': self.config.max_delay,
                'randomness': self.config.randomness,
            }
        }


# Convenience function
async def simulate_human_behavior(
    duration: float = 10.0,
    pattern: BehaviorPattern = BehaviorPattern.RESEARCHER
) -> Dict[str, Any]:
    """
    Quick human behavior simulation.
    
    Args:
        duration: Simulation duration in seconds
        pattern: Behavior pattern to use
        
    Returns:
        Simulation statistics
    """
    config = SimulationConfig(pattern=pattern)
    simulator = BehaviorSimulator(config)
    return await simulator.simulate_page_visit(read_time=duration)
