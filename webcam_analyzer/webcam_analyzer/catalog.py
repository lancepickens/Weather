"""Bright-star catalog and alt/az projection.

We vendor ~80 of the brightest stars (Vmag ≤ 3.0) in J2000 ICRS coordinates.
That's about all a 1/30-s security PTZ can plausibly see through Bay Area
light pollution; loading the full Yale BSC would be overkill.

For predicting which stars are above the horizon at a given observation
time, we lean on astropy's AltAz transformation (handles precession,
nutation, refraction, polar motion). Topocentric conversion needs the
observer's lat/lon/elevation and the timestamp.
"""
from __future__ import annotations

from dataclasses import dataclass

# (name, RA_deg J2000, Dec_deg J2000, Vmag) — brightest stars, source: Hipparcos / SIMBAD.
# Coords already converted from RA hh:mm:ss to decimal degrees.
BRIGHT_STARS: list[tuple[str, float, float, float]] = [
    ("Sirius",      101.2871, -16.7161, -1.46),
    ("Canopus",      95.9879, -52.6957, -0.74),
    ("Rigil Kent",  219.9021, -60.8340, -0.27),
    ("Arcturus",    213.9154,  19.1825, -0.05),
    ("Vega",        279.2347,  38.7837,  0.03),
    ("Capella",      79.1723,  45.9980,  0.08),
    ("Rigel",        78.6345,  -8.2017,  0.13),
    ("Procyon",     114.8255,   5.2250,  0.34),
    ("Achernar",     24.4285, -57.2367,  0.46),
    ("Betelgeuse",   88.7929,   7.4071,  0.42),
    ("Hadar",       210.9559, -60.3729,  0.61),
    ("Altair",      297.6958,   8.8683,  0.77),
    ("Acrux",       186.6496, -63.0991,  0.77),
    ("Aldebaran",    68.9802,  16.5093,  0.85),
    ("Antares",     247.3519, -26.4320,  1.06),
    ("Spica",       201.2983, -11.1614,  0.97),
    ("Pollux",      116.3289,  28.0262,  1.14),
    ("Fomalhaut",   344.4127, -29.6222,  1.16),
    ("Mimosa",      191.9303, -59.6888,  1.25),
    ("Deneb",       310.3580,  45.2803,  1.25),
    ("Regulus",     152.0930,  11.9672,  1.36),
    ("Adhara",      104.6564, -28.9721,  1.50),
    ("Castor",      113.6500,  31.8883,  1.58),
    ("Gacrux",      187.7915, -57.1132,  1.63),
    ("Shaula",      263.4022, -37.1038,  1.63),
    ("Bellatrix",    81.2828,   6.3497,  1.64),
    ("Elnath",       81.5728,  28.6075,  1.65),
    ("Miaplacidus", 138.3001, -69.7172,  1.69),
    ("Alnilam",      84.0534,  -1.2019,  1.69),
    ("Alnair",      332.0583, -46.9610,  1.74),
    ("Alnitak",      85.1897,  -1.9426,  1.74),
    ("Regor",       122.3833, -47.3367,  1.78),
    ("Alioth",      193.5074,  55.9598,  1.77),
    ("Kaus Australis", 276.0430, -34.3847, 1.85),
    ("Mirfak",       51.0808,  49.8612,  1.79),
    ("Dubhe",       165.9319,  61.7510,  1.79),
    ("Wezen",       107.0978, -26.3933,  1.83),
    ("Alkaid",      206.8852,  49.3133,  1.86),
    ("Sargas",      264.3297, -42.9978,  1.86),
    ("Avior",       125.6284, -59.5095,  1.86),
    ("Menkalinan",   89.8822,  44.9474,  1.90),
    ("Atria",       252.1662, -69.0277,  1.91),
    ("Alhena",      99.4279,  16.3993,  1.93),
    ("Peacock",     306.4119, -56.7351,  1.94),
    ("Polaris",      37.9544,  89.2641,  1.97),
    ("Mirzam",       95.6749, -17.9559,  1.98),
    ("Alphard",     141.8968,  -8.6586,  1.99),
    ("Algol",        47.0422,  40.9556,  2.10),
    ("Hamal",        31.7933,  23.4624,  2.00),
    ("Diphda",        10.8975,-17.9866,  2.04),
    ("Nunki",       283.8163, -26.2967,  2.05),
    ("Saiph",        86.9391,  -9.6697,  2.07),
    ("Mizar",       200.9814,  54.9254,  2.23),
    ("Schedar",       10.1268, 56.5373,  2.24),
    ("Caph",          2.2945,  59.1497,  2.27),
    ("Mirach",       17.4330,  35.6206,  2.06),
    ("Algenib",       3.3088,  15.1836,  2.83),
    ("Markab",      346.1903,  15.2053,  2.49),
    ("Scheat",      345.9436,  28.0828,  2.42),
    ("Alpheratz",    2.0967,  29.0905,  2.06),
    ("Almach",       30.9747,  42.3299,  2.10),
    ("Phecda",      178.4577,  53.6948,  2.41),
    ("Merak",       165.4602,  56.3825,  2.34),
    ("Megrez",      183.8565,  57.0326,  3.31),
    ("Denebola",    177.2649,  14.5720,  2.14),
    ("Algieba",     154.9931,  19.8415,  2.61),
    ("Cor Caroli",  194.0067,  38.3183,  2.81),
    ("Izar",        221.2467,  27.0742,  2.37),
    ("Zubeneschamali", 229.2517,-9.3829, 2.61),
    ("Rasalhague",  263.7335,  12.5600,  2.07),
    ("Rasalgethi",  258.6620,  14.3902,  3.06),
    ("Sabik",       257.5942, -15.7247,  2.43),
    ("Sadr",        305.5571,  40.2567,  2.23),
    ("Aljanah",     311.5526,  33.9703,  2.48),
    ("Albireo",     292.6803,  27.9597,  3.05),
    ("Etamin",      269.1515,  51.4889,  2.23),
    ("Eltanin",     269.1515,  51.4889,  2.23),
    ("Vindemiatrix",195.5440,  10.9591,  2.85),
    ("Mintaka",      83.0017,  -0.2991,  2.23),
    ("Menkar",       45.5699,   4.0897,  2.53),
    ("Enif",        326.0464,   9.8750,  2.39),
    ("Kornephoros", 247.5550,  21.4896,  2.78),
    ("Alphecca",    233.6722,  26.7148,  2.23),
]


@dataclass(frozen=True)
class StarPrediction:
    name: str
    ra_deg: float
    dec_deg: float
    vmag: float
    alt_deg: float
    az_deg: float


def predict_visible(
    timestamp_unix: float,
    lat_deg: float,
    lon_deg: float,
    elevation_m: float,
    min_alt_deg: float = 5.0,
    max_vmag: float = 3.0,
) -> list[StarPrediction]:
    """Return bright stars above ``min_alt_deg`` at the given epoch / location.

    Requires astropy. Coordinates returned in topocentric horizontal frame.
    Batches all stars into a single SkyCoord transform — per-star transform
    is ~30× slower at 80-star catalog sizes.
    """
    import astropy.units as u
    from astropy.coordinates import AltAz, EarthLocation, SkyCoord
    from astropy.time import Time

    eligible = [row for row in BRIGHT_STARS if row[3] <= max_vmag]
    if not eligible:
        return []
    names = [r[0] for r in eligible]
    ras = [r[1] for r in eligible]
    decs = [r[2] for r in eligible]
    vmags = [r[3] for r in eligible]

    loc = EarthLocation(lat=lat_deg * u.deg, lon=lon_deg * u.deg, height=elevation_m * u.m)
    obstime = Time(timestamp_unix, format="unix")
    altaz_frame = AltAz(obstime=obstime, location=loc)
    sc = SkyCoord(ra=ras * u.deg, dec=decs * u.deg, frame="icrs")
    aa = sc.transform_to(altaz_frame)
    alts = aa.alt.deg
    azs = aa.az.deg

    out: list[StarPrediction] = []
    for name, ra, dec, vmag, alt, az in zip(names, ras, decs, vmags, alts, azs):
        if alt < min_alt_deg:
            continue
        out.append(StarPrediction(name=name, ra_deg=ra, dec_deg=dec, vmag=vmag,
                                  alt_deg=float(alt), az_deg=float(az)))
    return out
