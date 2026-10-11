-- Shared helper functions.


-- Distance in nautical miles between two points on the earth.
--
-- This is the haversine formula. It treats the earth as a sphere, which is
-- close enough here - the error against the real (slightly squashed) shape
-- is a few tenths of a percent, far smaller than the uncertainty in the
-- fuel model.
--
-- It was written out by hand in the flight loader before; having it as a
-- function means the same formula is used everywhere, and the quality
-- checks can reuse it.
--
-- IMMUTABLE tells PostgreSQL the answer only ever depends on the inputs,
-- so it can cache and optimise calls. STRICT means it returns NULL if any
-- argument is NULL, which saves checking for that at every call site.
CREATE OR REPLACE FUNCTION core.haversine_nm(
    lat1 DOUBLE PRECISION,
    lon1 DOUBLE PRECISION,
    lat2 DOUBLE PRECISION,
    lon2 DOUBLE PRECISION
)
RETURNS DOUBLE PRECISION
LANGUAGE sql
IMMUTABLE
STRICT
AS $$
    SELECT 2 * 3440.065 * asin(sqrt(
        power(sin(radians(lat2 - lat1) / 2), 2)
        + cos(radians(lat1))
        * cos(radians(lat2))
        * power(sin(radians(lon2 - lon1) / 2), 2)
    ))
$$;
