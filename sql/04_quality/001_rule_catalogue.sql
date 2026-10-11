-- The list of data quality checks.
--
-- The OpenSky flight list is built automatically from transponder signals,
-- not from airline schedules. The origin and destination are worked out by
-- matching the start and end of each track to the nearest airport, which
-- fails whenever receiver coverage is thin. A fair share of rows come out
-- with a blank airport, a missing aircraft type, or a duration that makes
-- no sense.
--
-- So before any fuel numbers get calculated, it has to be clear which
-- flights can actually be trusted.
--
-- Two levels:
--   HARD = flight is dropped from the fuel analysis (but still counted)
--   SOFT = flight is kept but flagged
--
-- Both counts go in the writeup. Reporting that a given share of flights
-- was dropped, with a breakdown of why, is more convincing than presenting
-- a suspiciously clean dataset.
--
-- The checks live here as data rather than in Python so the dashboard can
-- show the description and the reason next to each flag.

INSERT INTO core.dim_quality_rule
    (rule_code, rule_name, rule_family, severity, description, rationale)
VALUES

-- COMPLETENESS: are the fields needed for the calculation actually there?

('COM_NO_ORIGIN',        'No departure airport',          'COMPLETENESS', 'HARD',
 'The origin column is blank.',
 'Without both airports there is no distance, and without distance there is no fuel figure.'),

('COM_NO_DESTINATION',   'No arrival airport',            'COMPLETENESS', 'HARD',
 'The destination column is blank.',
 'Same problem - no distance can be calculated, and the flight cannot be grouped into a route.'),

('COM_NO_TYPE',          'No aircraft type',              'COMPLETENESS', 'HARD',
 'The typecode column is blank.',
 'OpenAP needs an aircraft type to give a fuel figure, so there is nothing to calculate with.'),

('COM_NO_CALLSIGN',      'No callsign',                   'COMPLETENESS', 'SOFT',
 'The callsign column is blank.',
 'The airline is taken from the callsign, so without it the flight cannot be attributed to an operator.'),

-- IDENTITY: which aircraft and which airline is this, and is it really one?

('ID_AIRPORT_UNKNOWN',   'Airport code not in the reference data', 'IDENTITY', 'HARD',
 'The origin or destination code does not appear in the OurAirports dataset.',
 'No coordinates means no distance. Usually a disused code or a typo in the source.'),

('ID_TYPE_UNSUPPORTED',  'Aircraft type not covered by OpenAP', 'IDENTITY', 'SOFT',
 'The type is known but OpenAP does not model it and no similar type was assigned.',
 'Not really an error - but the share of traffic affected still needs reporting.'),

('ID_DUPLICATE_ICAO24',  'Same transponder code on overlapping flights', 'IDENTITY', 'HARD',
 'The same icao24 appears on two flights whose times overlap.',
 'One aircraft cannot fly two flights at once, so at least one of the rows is wrong.'),

('ID_CALLSIGN_CONFLICT', 'Same callsign on overlapping flights', 'IDENTITY', 'SOFT',
 'The same callsign is used by two aircraft at overlapping times.',
 'Airlines reuse callsigns through the day, which is fine, but not at the same moment.'),

-- CONSISTENCY: do the numbers agree with each other?

('CON_SAME_AIRPORT',     'Departure and arrival are the same', 'CONSISTENCY', 'SOFT',
 'The origin and destination codes are identical.',
 'Real enough - training circuits, test flights, diversions back to base - but not a normal service, so it would distort route averages.'),

('CON_ZERO_DISTANCE',    'Distance is zero',              'CONSISTENCY', 'HARD',
 'The calculated great-circle distance is zero or negative.',
 'Fuel per kilometre cannot be worked out, and it would divide by zero in the KPI view.'),

('CON_DURATION_SHORT',   'Flight is too short',           'CONSISTENCY', 'HARD',
 'The flight lasts less than the minimum in params.yml.',
 'Usually a fragment of a track that the source failed to join up properly.'),

('CON_DURATION_LONG',    'Flight is too long',            'CONSISTENCY', 'HARD',
 'The flight lasts longer than the maximum in params.yml.',
 'Normally means two separate flights got merged into one row.'),

('CON_SPEED_IMPLAUSIBLE','Implied speed makes no sense',  'CONSISTENCY', 'HARD',
 'Distance divided by duration gives an average speed outside the limits in params.yml.',
 'Either the distance or the duration is wrong, and there is no way to tell which.'),

('CON_ORIGIN_FAR',       'First position is far from the departure airport', 'CONSISTENCY', 'SOFT',
 'The first point the aircraft was seen at is further from the origin airport than the limit allows.',
 'Receiver coverage near the airport was poor, so the departure airport is a guess rather than an observation.'),

('CON_DESTINATION_FAR',  'Last position is far from the arrival airport', 'CONSISTENCY', 'SOFT',
 'The last point the aircraft was seen at is further from the destination airport than the limit allows.',
 'Same as above, at the other end of the flight.')

ON CONFLICT DO NOTHING;
