"""Working out fuel burn and emissions using OpenAP.

Fuel flow depends on how heavy the aircraft is, and it gets lighter as it
burns fuel. So the calculation has to go through the flight in order,
subtracting the fuel as it goes, rather than doing every segment at once.

The starting weight is an estimate based on the aircraft type and how far it
is flying. That estimate is the biggest source of error in the whole model,
so it needs to be written up properly.
"""
