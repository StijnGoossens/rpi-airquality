"""Self-check for the TVOC outlier maths. Run with: python src/test_utils.py"""

from utils import baseline_deviation

flat = [120.0] * 48

# Sitting exactly on the baseline is not a deviation.
assert baseline_deviation(flat, 120.0) == (120.0, 0.0)

# A small wiggle on a dead-flat history must not read as a spike: without the
# MAD floor the deviation scale here would be zero and this would divide by it.
assert baseline_deviation(flat, 125.0)[1] < 3.5

# A real spike clears the threshold.
assert baseline_deviation(flat, 400.0)[1] > 3.5

# Spikes already in the history must not drag the baseline up with them, which
# is the whole reason for using the median over the mean.
spiky = [120.0] * 40 + [900.0] * 8
assert baseline_deviation(spiky, 120.0)[0] == 120.0
assert baseline_deviation(spiky, 400.0)[1] > 3.5

# A falling reading deviates downwards, not upwards.
assert baseline_deviation(flat, 20.0)[1] < 0

# Too little history to know what normal is -- say so rather than guess.
assert baseline_deviation([120.0] * 5, 400.0) is None

# Gaps in the history are holes, not zeroes: a failed read must not pull the
# baseline down. float("nan") is what pandas hands over for a NULL column.
assert baseline_deviation(flat + [None] * 10 + [float("nan")] * 10, 120.0)[0] == 120.0

print("ok")
