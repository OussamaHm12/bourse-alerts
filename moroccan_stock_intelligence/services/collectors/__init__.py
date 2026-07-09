"""Phase 1b data collectors: macro (Bank Al-Maghrib) and the issuer page
(company profile + the six published ratios, from a single fetch).

Every collector is isolated: a failure leaves the feed empty, and the owning
analyst reports the data as unavailable rather than guessing a value.
"""

# Provenance recorded on every fundamentals row. A DERIVED_SOURCE row holds a PER
# computed as price / BPA because the published cell was "-". It never overwrites
# an official value, and the analyst presents it as inference, never as fact.
OFFICIAL_SOURCE = "Casablanca Bourse"
DERIVED_SOURCE = "derived"
