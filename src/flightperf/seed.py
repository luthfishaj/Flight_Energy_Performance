"""Fills in the date table.

This is the one dimension table that gets generated rather than downloaded.
"""


def seed_dim_date(con, start, end):
    """Fill core.dim_date with one row per day between start and end.

    Both dates are strings like "2022-01-01" and both ends are included.

    It is built in SQL rather than pandas because generate_series does the
    job in one statement and it keeps the logic next to the rest of the SQL.
    Power BI also needs a real date table for its date filters to work.

    Running this twice will not create duplicates - the ON CONFLICT at the
    bottom skips any day that is already there.
    """
    with con.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.dim_date
            SELECT
                CAST(to_char(d, 'YYYYMMDD') AS INTEGER)      AS date_key,
                d::DATE                                       AS full_date,
                EXTRACT(year    FROM d)::SMALLINT             AS year,
                EXTRACT(quarter FROM d)::SMALLINT             AS quarter,
                EXTRACT(month   FROM d)::SMALLINT             AS month,
                trim(to_char(d, 'Month'))                     AS month_name,
                EXTRACT(day     FROM d)::SMALLINT             AS day_of_month,
                EXTRACT(isodow  FROM d)::SMALLINT             AS day_of_week,
                trim(to_char(d, 'Day'))                       AS day_name,
                EXTRACT(isodow FROM d) >= 6                   AS is_weekend,
                EXTRACT(week    FROM d)::SMALLINT             AS iso_week,

                -- Airlines plan in summer and winter seasons rather than
                -- calendar quarters. Summer officially starts on the last
                -- Sunday of March, but April to October is close enough for
                -- grouping charts. Worth fixing if exact dates are needed.
                CASE WHEN EXTRACT(month FROM d) BETWEEN 4 AND 10
                     THEN 'S' ELSE 'W' END
                    || to_char(d, 'YY')                       AS season_iata
            FROM generate_series(
                CAST(%s AS DATE),
                CAST(%s AS DATE),
                INTERVAL '1 day'
            ) AS t(d)
            ON CONFLICT (date_key) DO NOTHING
            """,
            [start, end],
        )

        cur.execute("SELECT count(*) FROM core.dim_date")
        total = cur.fetchone()[0]

    con.commit()
    return total
