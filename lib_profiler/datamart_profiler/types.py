# Column types

#: No data (whole column is missing)
MISSING_DATA = 'https://metadata.datadrivendiscovery.org/types/MissingData'

#: Integer (numbers without a decimal point)
INTEGER = 'http://schema.org/Integer'

#: Floating-point numbers
FLOAT = 'http://schema.org/Float'

#: Text, better represented as strings
TEXT = 'http://schema.org/Text'

#: Booleans, e.g. only the two values "true" and "false"
BOOLEAN = 'http://schema.org/Boolean'

#: Numerical values representing latitude coordinates
LATITUDE = 'http://schema.org/latitude'

#: Numerical values representing longitude coordinates
LONGITUDE = 'http://schema.org/longitude'

#: A specific instant in time (not partial ones such as "July 4" or "12am")
DATE_TIME = 'http://schema.org/DateTime'

#: The street address of a location
ADDRESS = 'http://schema.org/address'

#: A named administrative area, such as a country, state, or city
ADMIN = 'http://schema.org/AdministrativeArea'

#: An identifier
ID = 'http://schema.org/identifier'

#: Categorical values, i.e. drawn from a limited number of options
CATEGORICAL = 'http://schema.org/Enumeration'

#: A geographic location (longitude+latitude coordinates, in that order)
GEO_POINT = 'http://schema.org/GeoCoordinates'

#: A geographic location (latitude+longitude coordinates, in that order)
GEO_POINT_LATLONG = 'http://schema.org/GeoCoordinates_LatLong'

#: A geographic shape described by its coordinates
GEO_POLYGON = 'http://schema.org/GeoShape'


# Dataset types

DATASET_NUMERICAL = 'numerical'
DATASET_CATEGORICAL = 'categorical'
DATASET_SPATIAL = 'spatial'
DATASET_TEMPORAL = 'temporal'
