from pathlib import Path


def valid_config_data(root: Path) -> dict[str, object]:
    """Return a fresh valid experiment mapping rooted below ``root``."""

    return {
        "run": {
            "directory": str(root / "run"),
            "minimum_free_bytes": 1_048_576,
            "master_seed": 12345,
            "final_seed": 54321,
        },
        "target": {
            "image": "curlimages/curl:8.10.1",
            "argv": ["https://example.invalid/data"],
            "environment": {"LANG": "C"},
            "working_directory": "/work",
            "mounts": [],
        },
        "capture": {
            "image": "trafficlab-capture:local",
            "network_probe_url": "https://example.invalid/",
            "readiness_timeout_seconds": 10.0,
            "workload_timeout_seconds": 30.0,
            "flush_timeout_seconds": 5.0,
            "total_timeout_seconds": 60.0,
        },
        "generation": {
            "trial": {"max_packets": 2_000, "max_output_bytes": 4_000_000, "max_wall_seconds": 5.0},
            "final": {"max_packets": 20_000, "max_output_bytes": 40_000_000, "max_wall_seconds": 30.0},
        },
        "genetic": {
            "population_size": 9,
            "generation_count": 3,
            "tournament_size": 3,
            "elite_count": 1,
            "trial_seeds": [101, 102],
            "duplicate_mutation_attempts": 3,
            "early_stopping_generations": 0,
            "resume": False,
        },
        "models": {
            "enabled": ["poisson_empirical", "markov_renewal", "mmpp"],
            "poisson_empirical": {
                "crossover_probability": 0.9,
                "mutation_probability": 1.0,
                "mutation_scale": 0.1,
                "c_lambda": {"lower": 0.25, "upper": 4.0},
            },
            "markov_renewal": {
                "crossover_probability": 0.9,
                "mutation_probability": 0.2,
                "mutation_scale": 0.1,
                "q1": {"lower": 0.1, "upper": 0.4},
                "q2": {"lower": 0.6, "upper": 0.9},
                "alpha": {"lower": 0.0, "upper": 2.0},
                "r": {"lower": 1, "upper": 8},
                "c_t": {"lower": 0.25, "upper": 4.0},
            },
            "mmpp": {
                "crossover_probability": 0.9,
                "mutation_probability": 0.25,
                "mutation_scale": 0.1,
                "q01": {"lower": 0.01, "upper": 10.0},
                "q10": {"lower": 0.01, "upper": 10.0},
                "lambda0": {"lower": 0.01, "upper": 100.0},
                "lambda1": {"lower": 0.1, "upper": 1_000.0},
            },
        },
        "similarity": {
            "iat_diagnostic_quantile": 0.95,
            "acf_lags": [1],
            "acf_lag_weights": [1.0],
            "acf_iat_weight": 0.5,
            "acf_size_weight": 0.5,
            "multiscale_widths_seconds": [0.1, 1.0],
            "multiscale_scale_weights": [0.5, 0.5],
            "multiscale_packet_weight": 0.5,
            "multiscale_byte_weight": 0.5,
            "max_direction_bin_cells": 100_000,
            "method_weights": {
                "frame_size_ks": 0.25,
                "iat_ks": 0.25,
                "autocorrelation": 0.25,
                "multiscale_rate": 0.25,
            },
        },
    }
