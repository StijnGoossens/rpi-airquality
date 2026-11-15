"""Self-checks for the TVOC outlier maths and the room-line smoothing. Run with: python src/test_utils.py"""

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

# smooth_steps: a sensor that reports 15.5 every 15 minutes for 4 hours, steps to
# 15.7 for 4 hours, then goes silent for 5 hours and comes back at 15.9.
import pandas as pd  # noqa: E402 (only this check needs pandas)

from utils import smooth_steps  # noqa: E402

t0 = pd.Timestamp("2026-11-15 00:00", tz="Europe/Brussels")
reports = pd.concat(
    [
        pd.Series(15.5, pd.date_range(t0, periods=16, freq="15min")),
        pd.Series(15.7, pd.date_range(t0 + pd.Timedelta("4h"), periods=16, freq="15min")),
        pd.Series(15.9, pd.date_range(t0 + pd.Timedelta("13h"), periods=4, freq="15min")),
    ]
)
smooth = smooth_steps(reports)

# Never outside what was reported, and flat where the reports were flat.
assert smooth.between(15.5, 15.9).all()
assert smooth[t0 + pd.Timedelta("2h")] == 15.5
assert smooth[t0 + pd.Timedelta("6h")] == 15.7
# The step itself is softened, not moved: halfway through it sits in between.
assert 15.5 < smooth[t0 + pd.Timedelta("4h")] < 15.7
# A silence is a gap, not an invented steady reading.
assert t0 + pd.Timedelta("10h") not in smooth.index

print("ok")
