from pathlib import Path


def acd_config_data() -> dict[str, object]:
    """Return the explicit non-default settings for one enabled ACD family."""
    return {
        "crossover_probability": 0.9,
        "mutation_probability": 1.0,
        "mutation_scale": 0.1,
        "order": {"lower": 1, "upper": 3},
    }


def nhpp_config_data() -> dict[str, object]:
    """Return the explicit non-default settings for one enabled NHPP family."""
    return {
        "crossover_probability": 0.9,
        "mutation_probability": 1.0,
        "mutation_scale": 0.1,
        "bin_count": {"lower": 2, "upper": 16},
    }


def markov_packet_train_config_data() -> dict[str, object]:
    """Return explicit settings for one opt-in Markov packet-train family."""
    return {
        "crossover_probability": 0.9,
        "mutation_probability": 1.0,
        "mutation_scale": 0.1,
        "length_cap": {"lower": 3, "upper": 8},
    }


def packet_hmm_config_data() -> dict[str, object]:
    """Return explicit settings for the opt-in categorical packet HMM."""
    return {
        "crossover_probability": 0.9,
        "mutation_probability": 1.0,
        "mutation_scale": 0.1,
        "state_count": {"lower": 2, "upper": 4},
    }


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
            "cvm_iat_weight": 0.5,
            "cvm_size_weight": 0.5,
            "ad_iat_weight": 0.5,
            "ad_size_weight": 0.5,
            "js_iat_bin_count": 8,
            "js_iat_weight": 0.5,
            "js_mark_weight": 0.5,
            "mmd_feature_count": 16,
            "mmd_seed": 2026,
            "mmd_scale_floor": 0.001,
            "postfit": {
                "dispersion": {
                    "widths_seconds": [0.25, 1.0],
                    "scale_weights": [0.5, 0.5],
                    "fano_weight": 0.5,
                    "allan_weight": 0.5,
                },
                "transition": {
                    "size_bin_count": 2,
                    "iat_bin_count": 2,
                    "pseudocount": 0.5,
                    "occupancy_weight": 0.34,
                    "transition_rows_weight": 0.33,
                    "runs_weight": 0.33,
                },
                "c2st": {
                    "feature_version": "window-v1",
                    "window_width_seconds": 0.25,
                    "fold_count": 3,
                    "guard_window_count": 1,
                    "maximum_window_count": 4096,
                    "l2_regularization": 1.0,
                    "maximum_iterations": 200,
                    "tolerance": 1e-9,
                },
            },
            "method_weights": {
                "frame_size_ks": 0.125,
                "iat_ks": 0.125,
                "autocorrelation": 0.125,
                "multiscale_rate": 0.125,
                "cramer_von_mises": 0.125,
                "anderson_darling": 0.125,
                "jensen_shannon": 0.125,
                "approximate_mmd": 0.125,
            },
        },
    }
