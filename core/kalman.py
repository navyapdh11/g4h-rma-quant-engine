"""
Multivariate Kalman Filter — Production Implementation V6.0
=======================================================
State vector:  x = [beta, alpha]^T
Transition:    x_k = x_{k-1} + w   (random walk, F = I)
Observation:   z = Price_A
Design:        H = [Price_B, 1]

V6.0 Enhancements:
  - Divergence detection via condition number monitoring
  - State validation at each step
  - Graceful degradation on numerical issues
  - Comprehensive logging for debugging
"""
from __future__ import annotations
import numpy as np
from dataclasses import dataclass
from typing import Optional
from config import KalmanConfig
import logging

logger = logging.getLogger(__name__)


@dataclass
class KalmanSnapshot:
    """Immutable point-in-time state of the filter."""
    beta: float
    alpha: float
    spread: float
    innovation_var: float
    pure_z_score: float
    step: int
    converged: bool
    is_divergent: bool = False
    condition_number: float = 1.0


class MultivariateKalmanFilter:
    """
    2D Kalman Filter for dynamic hedge ratio estimation.
    Models:  Price_A(t) = beta(t) * Price_B(t) + alpha(t) + eps(t)
    """
    WARMUP_STEPS: int = 30
    MAX_CONDITION_NUMBER: float = 1e10
    MIN_INNOVATION_VAR: float = 1e-8

    def __init__(self, cfg: Optional[KalmanConfig] = None):
        self.cfg = cfg or KalmanConfig()
        self.WARMUP_STEPS = self.cfg.warmup_steps
        self.x = np.array([[self.cfg.initial_beta],
                           [self.cfg.initial_alpha]], dtype=np.float64)
        self.P = np.eye(2, dtype=np.float64) * self.cfg.initial_covariance
        self.Q = np.diag(np.array([self.cfg.process_noise_beta,
                                   self.cfg.process_noise_alpha], dtype=np.float64))
        self.R_val = self.cfg.measurement_noise
        self._step = 0
        self._last_S = 1.0
        self._last_spread = 0.0
        self._convergence_history: list = []

    def step(self, price_a: float, price_b: float) -> KalmanSnapshot:
        """Execute one full predict-update cycle with validation."""
        self._step += 1
        
        if not np.isfinite(price_a) or not np.isfinite(price_b):
            logger.warning(f"Invalid input: price_a={price_a}, price_b={price_b}")
            return self._create_invalid_snapshot()
        
        # PREDICT
        x_pred = self.x.copy()
        P_pred = self.P + self.Q
        
        # MEASUREMENT
        z = np.array([[price_a]], dtype=np.float64)
        H = np.array([[price_b, 1.0]], dtype=np.float64)
        
        # Innovation
        y = z - H @ x_pred
        S = (H @ P_pred @ H.T).item() + self.R_val
        S = max(S, self.MIN_INNOVATION_VAR)
        
        # Kalman Gain
        PHt = P_pred @ H.T
        K = PHt / S
        
        # Update state
        self.x = x_pred + K * y
        
        # JOSEPH FORM covariance update
        I_KH = np.eye(2) - K @ H
        self.P = I_KH @ P_pred @ I_KH.T + K * self.R_val * K.T
        self.P = 0.5 * (self.P + self.P.T)
        
        # Check condition number
        try:
            eigvals = np.linalg.eigvalsh(self.P)
            condition_number = max(eigvals) / max(min(eigvals), 1e-10)
        except np.linalg.LinAlgError:
            condition_number = float('inf')
            logger.warning("Eigenvalue computation failed")
        
        # Cap eigenvalues
        if np.max(eigvals) > self.cfg.max_eigenvalue:
            logger.warning(f"Kalman divergence detected — resetting")
            self.P = np.eye(2) * self.cfg.initial_covariance
            condition_number = 1.0
        
        spread = float(y.flatten()[0])
        self._last_S = S
        self._last_spread = spread
        pure_z = spread / np.sqrt(S) if S > 0 else 0.0
        
        converged = self._step > self.WARMUP_STEPS
        z_safe = pure_z if converged else 0.0
        
        # Track convergence
        self._convergence_history.append(abs(pure_z))
        if len(self._convergence_history) > 100:
            self._convergence_history.pop(0)
        
        is_divergent = False
        if len(self._convergence_history) >= 50:
            rolling_var = np.var(self._convergence_history[-50:])
            if rolling_var > 50.0:
                is_divergent = True
                logger.debug(f"Kalman divergence: rolling_var={rolling_var:.2f}")
        
        return KalmanSnapshot(
            beta=float(self.x[0, 0]),
            alpha=float(self.x[1, 0]),
            spread=spread,
            innovation_var=S,
            pure_z_score=z_safe,
            step=self._step,
            converged=converged,
            is_divergent=is_divergent,
            condition_number=condition_number,
        )
    
    def _create_invalid_snapshot(self) -> KalmanSnapshot:
        return KalmanSnapshot(
            beta=0.0, alpha=0.0, spread=0.0,
            innovation_var=1.0, pure_z_score=0.0,
            step=self._step, converged=False,
            is_divergent=True, condition_number=float('inf')
        )

    @property
    def current_beta(self) -> float:
        return float(self.x[0, 0])
    
    @property
    def current_alpha(self) -> float:
        return float(self.x[1, 0])
    
    @property
    def is_converged(self) -> bool:
        return self._step > self.WARMUP_STEPS
    
    @property
    def is_healthy(self) -> bool:
        if len(self._convergence_history) < 50:
            return True
        return np.var(self._convergence_history[-50:]) < 10.0

    def reset(self):
        self.__init__(self.cfg)
    
    def get_state_dict(self) -> dict:
        return {
            "beta": self.current_beta,
            "alpha": self.current_alpha,
            "step": self._step,
            "converged": self.is_converged,
            "healthy": self.is_healthy,
        }
