"""Compatibility entry point; legacy training data is intentionally rejected."""
from ml.training.train_temporal import (
    chronological_folds,
    load_training_dataset,
    main,
    run_training,
    select_model_from_folds,
    validate_dataset,
)

__all__ = [
    "chronological_folds",
    "load_training_dataset",
    "main",
    "run_training",
    "select_model_from_folds",
    "validate_dataset",
]

if __name__ == "__main__":
    main()
