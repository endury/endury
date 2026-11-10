"""Riegel race-time prediction for running.

Pure functions — stdlib only, no I/O. Riegel's endurance model (Riegel,
1981) predicts the time for one race distance from a known performance at
another::

    T2 = T1 * (D2 / D1) ** k

``k`` is the fatigue factor: how much slower an athlete gets as the
distance doubles. Riegel fitted ``k = 1.06`` across running world-class
and age-group data; 1.05 to 1.08 is the practical spread, the low end for
athletes with a deep endurance base, the high end for speed-biased ones.

Validity bounds — this is a curve fit to race data, not a physiological
law, and it is honest to state where it stops working:

* **Running only.** The exponent is calibrated on running. It is not
  valid for cycling (drafting, terrain, and power-duration curves break
  the assumption) or swimming. This module deliberately makes **no
  multisport or triathlon prediction**: an overall triathlon time is a
  function of three discipline paces plus transitions, pacing strategy,
  and nutrition, which a single-exponent power law cannot express. Out
  of scope on purpose.
* **Extrapolation degrades with the distance ratio.** Predictions hold
  reasonably within roughly a 4x span of the known distance
  (:data:`RELIABLE_EXTRAPOLATION_RATIO`). Beyond that — 5 km to a
  marathon is 8.4x — the model is systematically optimistic: it knows
  nothing about glycogen depletion, fuelling, or musculoskeletal
  durability over three-plus hours.
* **It assumes the endurance training the target distance requires.** A
  fast 5 km runner who has never run beyond 15 km will not hit the
  predicted marathon time; the prediction describes an athlete equally
  prepared at both distances.
* **It assumes comparable conditions** — similar course profile, surface,
  altitude, weather, and pacing discipline — and that the known
  performance was a genuine race or all-out time trial, not a training
  run.

> Riegel, P.S. (1981). Athletic Records and Human Endurance.
> *American Scientist*, 69(3), 285-290.
"""

from __future__ import annotations

#: Riegel's fitted fatigue exponent for running.
RIEGEL_EXPONENT = 1.06

#: Accepted exponent range — outside this the "model" is no longer Riegel's.
MIN_EXPONENT = 1.0
MAX_EXPONENT = 1.15

#: Distance ratio beyond which the prediction is flagged as unreliable.
RELIABLE_EXTRAPOLATION_RATIO = 4.0

#: The standard road-running distances, in metres.
STANDARD_RUNNING_DISTANCES_M: dict[str, float] = {
    "5k": 5000.0,
    "10k": 10000.0,
    "half_marathon": 21097.5,
    "marathon": 42195.0,
}


def predict_race_time(
    known_distance_m: float,
    known_time_s: float,
    target_distance_m: float,
    exponent: float = RIEGEL_EXPONENT,
) -> float:
    """Predict a running race time with Riegel's power law.

    Args:
        known_distance_m: Distance of the known performance, in metres.
        known_time_s: Time of the known performance, in seconds.
        target_distance_m: Distance to predict, in metres.
        exponent: Riegel fatigue factor (default 1.06, running).

    Returns:
        Predicted time for ``target_distance_m``, in seconds.

    Raises:
        ValueError: If a distance or the time is not positive, or the
            exponent falls outside 1.00 to 1.15.
    """
    if known_distance_m <= 0 or target_distance_m <= 0:
        raise ValueError("Distances must be positive (metres).")
    if known_time_s <= 0:
        raise ValueError("Known time must be positive (seconds).")
    if not MIN_EXPONENT <= exponent <= MAX_EXPONENT:
        raise ValueError(
            f"Riegel exponent must be between {MIN_EXPONENT} and {MAX_EXPONENT}."
        )
    return known_time_s * float((target_distance_m / known_distance_m) ** exponent)


def predict_standard_distances(
    known_distance_m: float,
    known_time_s: float,
    exponent: float = RIEGEL_EXPONENT,
) -> dict[str, float]:
    """Predict all standard road-running distances from one performance.

    Args:
        known_distance_m: Distance of the known performance, in metres.
        known_time_s: Time of the known performance, in seconds.
        exponent: Riegel fatigue factor (default 1.06, running).

    Returns:
        Predicted seconds keyed by the names in
        :data:`STANDARD_RUNNING_DISTANCES_M` (5k, 10k, half_marathon,
        marathon).

    Raises:
        ValueError: Propagated from :func:`predict_race_time`.
    """
    return {
        name: predict_race_time(known_distance_m, known_time_s, distance, exponent)
        for name, distance in STANDARD_RUNNING_DISTANCES_M.items()
    }


def extrapolation_ratio(known_distance_m: float, target_distance_m: float) -> float:
    """Return how far a prediction reaches from the known distance.

    Args:
        known_distance_m: Distance of the known performance, in metres
            (must be positive).
        target_distance_m: Predicted distance, in metres (must be
            positive).

    Returns:
        The larger of the two distances divided by the smaller, so
        predicting up or down is treated symmetrically: 1.0 means same
        distance, 8.4 means a 5 km to marathon reach.
    """
    return max(known_distance_m, target_distance_m) / min(
        known_distance_m, target_distance_m
    )
