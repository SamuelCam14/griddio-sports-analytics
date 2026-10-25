"""
Bounded Logistic Regression with monotonic sign constraints.

Enforces strictly non-positive coefficients for home injury features (c <= 0)
and strictly non-negative coefficients for away injury features (c >= 0).
This mathematically prevents collinearity suppression where injuries erroneously
increase a team's win probability.
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import minimize
from sklearn.base import BaseEstimator, ClassifierMixin


class BoundedLogisticRegression(BaseEstimator, ClassifierMixin):
    """
    Logistic Regression with coordinate-wise bound constraints using L-BFGS-B.
    Conforms to scikit-learn's BaseEstimator and ClassifierMixin interfaces.
    """

    def __init__(
        self,
        C: float = 0.3,
        max_iter: int = 2000,
        feature_names: list[str] | None = None,
    ):
        self.C = C
        self.max_iter = max_iter
        self.feature_names = feature_names

    def fit(self, X, y):
        self.classes_ = np.unique(y)
        n_features = X.shape[1]

        bounds = []
        names = self.feature_names or []
        for i in range(n_features):
            fname = names[i] if i < len(names) else ""
            if fname.startswith("home_") and "lost_pct" in fname:
                bounds.append((-np.inf, 0.0))
            elif fname.startswith("away_") and "lost_pct" in fname:
                bounds.append((0.0, np.inf))
            else:
                bounds.append((-np.inf, np.inf))
        bounds.append((-np.inf, np.inf))  # Intercept

        y_arr = np.asarray(y, dtype=float)
        X_arr = np.asarray(X, dtype=float)
        n_samples = len(y_arr)

        def loss_and_grad(w):
            weights = w[:-1]
            intercept = w[-1]
            z = np.clip(np.dot(X_arr, weights) + intercept, -30, 30)
            p = 1.0 / (1.0 + np.exp(-z))
            eps = 1e-15
            p_safe = np.clip(p, eps, 1.0 - eps)

            # NLL + L2 penalty
            nll = -np.mean(y_arr * np.log(p_safe) + (1.0 - y_arr) * np.log(1.0 - p_safe))
            l2 = (1.0 / (2.0 * max(1e-5, self.C))) * np.sum(weights ** 2) / n_samples

            # Analytical gradient
            diff = (p - y_arr) / n_samples
            grad_w = np.dot(X_arr.T, diff) + (weights / (max(1e-5, self.C) * n_samples))
            grad_b = np.sum(diff)
            return nll + l2, np.append(grad_w, grad_b)

        w0 = np.zeros(n_features + 1)
        res = minimize(
            loss_and_grad,
            w0,
            jac=True,
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": self.max_iter},
        )
        self.coef_ = res.x[:-1].reshape(1, -1)
        self.intercept_ = np.array([res.x[-1]])
        self.n_iter_ = np.array([res.nit])
        return self

    def predict_proba(self, X):
        X_arr = np.asarray(X, dtype=float)
        z = np.clip(np.dot(X_arr, self.coef_[0]) + self.intercept_[0], -30, 30)
        p1 = 1.0 / (1.0 + np.exp(-z))
        return np.column_stack([1.0 - p1, p1])

    def predict(self, X):
        return (self.predict_proba(X)[:, 1] >= 0.5).astype(int)
