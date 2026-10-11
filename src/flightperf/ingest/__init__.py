"""Downloading the raw data.

One module per source (OurAirports, OpenSky, EUROCONTROL, DGAC, ERA5).
Each one saves the file into data/raw exactly as it arrived and then loads
it into a staging table. The raw files are never edited, so if the
processing turns out to be wrong it can be fixed without downloading
everything again.
"""
