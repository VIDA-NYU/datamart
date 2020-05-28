#!/usr/bin/env python3
import csv
import functools
import json
import logging
import os
import pickle
import requests
import sys
from urllib.parse import urlencode


logger = logging.getLogger()


def sparql_query(query):
    """Get results from the Wikidata SparQL endpoint.
    """
    url = 'https://query.wikidata.org/sparql?' + urlencode({
        'query': query,
    })
    logger.info("Querying: %s", url)
    response = requests.get(
        url,
        headers={
            'Accept': 'application/sparql-results+json',
            'User-Agent': 'Auctus',
        },
    )
    response.raise_for_status()
    obj = response.json()
    results = obj['results']['bindings']
    logger.info("SparQL: %d results", len(results))
    return results


def makes_file(name):
    def wrapper(func):
        @functools.wraps(func)
        def wrapped():
            if os.path.exists(name):
                logger.info("Skipping: %s", func.__doc__.splitlines()[0])
                return
            logger.info("Running: %s", func.__doc__.splitlines()[0])
            try:
                with open(name, 'w', newline='', encoding='utf-8') as fp:
                    writer = csv.writer(fp)
                    func(writer)
            except BaseException:
                os.remove(name)
                raise
        return wrapped
    return wrapper


def literal(item):
    assert item['type'] == 'literal'
    return item['value']


def q_entity_uri(item):
    assert item['type'] == 'uri'
    prefix = 'http://www.wikidata.org/entity/'
    value = item['value']
    assert value.startswith(prefix)
    return value[len(prefix):]


def uri(item):
    assert item['type'] == 'uri'
    return item['value']


@makes_file('countries.csv')
def countries(writer):
    """Get all countries with label and standard codes.
    """
    rows = sparql_query(
        'SELECT ?item ?itemLabel ?fips ?iso2 ?iso3\n'
        'WHERE\n'
        '{\n'
        '  ?item wdt:P31 wd:Q6256.\n'  # item "instance of" "country"
        '  ?item wdt:P901 ?fips.\n'  # item "FIPS 10-4 (countries and region)"
        '  ?item wdt:P297 ?iso2.\n'  # item "ISO 3166-1 alpha-2 code"
        '  ?item wdt:P298 ?iso3.\n'  # item "ISO 3166-1 alpha-3 code"
        '  MINUS{ ?item wdt:P31 wd:Q3024240. }\n'  # not "historical country"
        '  SERVICE wikibase:label {\n'
        '    bd:serviceParam wikibase:language "[AUTO_LANGUAGE],en".\n'
        '  }\n'
        '}\n'
    )

    writer.writerow(['country', 'label', 'fips', 'iso2', 'iso3'])
    for row in rows:
        value = q_entity_uri(row['item'])
        label = literal(row['itemLabel'])
        fips = literal(row['fips'])
        iso2 = literal(row['iso2'])
        iso3 = literal(row['iso3'])

        writer.writerow([value, label, fips, iso2, iso3])


@makes_file('geoshapes0.csv')
def geoshapes0(writer):
    """Get all countries with their geometry.
    """
    rows = sparql_query(
        'SELECT ?area ?shape\n'
        'WHERE\n'
        '{\n'
        '  ?area wdt:P31 wd:Q6256.\n'  # area "instance of" "country"
        '  ?area wdt:P3896 ?shape.\n'  # area "geoshape" shape
        '  MINUS{ ?area wdt:P31 wd:Q3024240. }\n'  # not "historical country"
        '}\n'
    )

    writer.writerow(['admin', 'geoshape URL', 'geoshape'])
    for row in rows:
        area = q_entity_uri(row['area'])
        shape_uri = uri(row['shape'])

        # FIXME: Work around Wikidata bug: '+' in URL needs to be '_'
        last_slash = shape_uri.index('/')
        if '+' in shape_uri[last_slash + 1:]:
            shape_uri = (
                shape_uri[:last_slash + 1] +
                shape_uri[last_slash + 1:].replace('+', '_')
            )

        try:
            logger.info("Getting geoshape %s", shape_uri)
            shape_resp = requests.get(shape_uri)
            shape_resp.raise_for_status()
            shape = json.dumps(
                shape_resp.json(),
                # Compact
                sort_keys=True, indent=None, separators=(',', ':'),
            )
        except requests.exceptions.HTTPError as e:
            logger.error("Error getting geoshape: %s", e)
            shape = None

        writer.writerow([area, shape_uri, shape])


@makes_file('geoshapes1.csv')
def geoshapes1(writer):
    """Get all countries with their geometry.
    """
    rows = sparql_query(
        'SELECT ?area ?shape\n'
        'WHERE\n'
        '{\n'
        # parent "instance of" "country" (country = admin level 0)
        '  ?parent wdt:P31 wd:Q6256.\n'
        # parent "contains administrative territorial entity" area
        '  ?parent wdt:P150 ?area.\n'
        # area "instance of" ["subclass of" "admin level 1"]
        '  ?area wdt:P31 [wdt:P279 wd:Q10864048].\n'
        # area "geoshape" shape
        '  ?area wdt:P3896 ?shape.\n'
        # parent not "historical country"
        '  MINUS{ ?parent wdt:P31 wd:Q3024240. }\n'
        '}\n'
    )

    writer.writerow(['admin', 'geoshape URL', 'geoshape'])
    for row in rows:
        area = q_entity_uri(row['area'])
        shape_uri = uri(row['shape'])

        # FIXME: Work around Wikidata bug: '+' in URL needs to be '_'
        last_slash = shape_uri.index('/')
        if '+' in shape_uri[last_slash + 1:]:
            shape_uri = (
                shape_uri[:last_slash + 1] +
                shape_uri[last_slash + 1:].replace('+', '_')
            )

        try:
            logger.info("Getting geoshape %s", shape_uri)
            shape_resp = requests.get(shape_uri)
            shape_resp.raise_for_status()
            shape = json.dumps(
                shape_resp.json(),
                # Compact
                sort_keys=True, indent=None, separators=(',', ':'),
            )
        except requests.exceptions.HTTPError as e:
            logger.error("Error getting geoshape: %s", e)
            shape = None

        writer.writerow([area, shape_uri, shape])


def get_shape_points(shape):
    points = []
    for feature in shape['data']['features']:
        assert feature['type'] == 'Feature'
        geometry = feature['geometry']
        if geometry['type'] == 'Polygon':
            for polygon in geometry['coordinates']:
                for point in polygon:
                    points.append(point)
        elif geometry['type'] == 'MultiPolygon':
            for multipolygon in geometry['coordinates']:
                for polygon in multipolygon:
                    for point in polygon:
                        points.append(point)
        else:
            raise AssertionError("Unrecognized geometry %r" % geometry['type'])
    return points


@makes_file('bounds0.csv')
def bounds0(writer):
    """Make bounding boxes from level 0 geometry.
    """
    with open('geoshapes0.csv') as fp:
        reader = iter(csv.reader(fp))
        assert next(reader) == ['admin', 'geoshape URL', 'geoshape']
        writer.writerow([
            'admin',
            'min long', 'max long',
            'min lat', 'max lat',
        ])

        for row in reader:
            area, shape_uri, shape = row
            shape = json.loads(shape)
            points = get_shape_points(shape)
            merged = (
                points[0][0], points[0][0],
                points[0][1], points[0][1],
            )
            for point in points[1:]:
                merged = (
                    min(merged[0], point[0]),
                    max(merged[1], point[0]),
                    min(merged[2], point[1]),
                    max(merged[3], point[1]),
                )
            writer.writerow([
                area, merged[0], merged[1], merged[2], merged[3],
            ])


@makes_file('bounds1.csv')
def bounds1(writer):
    """Make bounding boxes from level 1 geometry.
    """
    # Read all administrative areas
    all_admins = set()
    with open('areas1.csv') as fp:
        reader = iter(csv.reader(fp))
        assert next(reader) == ['parent', 'admin', 'admin level', 'admin name']
        for row in reader:
            all_admins.add(row[1])
    remaining_admins = set(all_admins)

    with open('geoshapes1.csv') as fp:
        reader = iter(csv.reader(fp))
        assert next(reader) == ['admin', 'geoshape URL', 'geoshape']
        writer.writerow([
            'admin',
            'min long', 'max long',
            'min lat', 'max lat',
        ])

        for row in reader:
            area, shape_uri, shape = row
            assert area in all_admins
            if area not in remaining_admins:
                logger.warning("Duplicate shape for %s", area)
                continue
            remaining_admins.discard(area)
            shape = json.loads(shape)
            points = get_shape_points(shape)
            merged = (
                points[0][0], points[0][0],
                points[0][1], points[0][1],
            )
            for point in points[1:]:
                merged = (
                    min(merged[0], point[0]),
                    max(merged[1], point[0]),
                    min(merged[2], point[1]),
                    max(merged[3], point[1]),
                )
            writer.writerow([
                area, merged[0], merged[1], merged[2], merged[3],
            ])

    # Read the missing shapes from OSM
    if not remaining_admins:
        return

    # To be able to resume if failed, build a pickle file first
    if os.path.isfile('.bounds1.pkl'):
        with open('.bounds1.pkl', 'rb') as fp:
            osm_cache = pickle.load(fp)
    else:
        osm_cache = {}
    try:
        logger.info(
            "Missing %d admin1 shapes, will get from OSM (%d in cache)",
            len(remaining_admins), len(osm_cache),
        )

        rows = sparql_query(
            'SELECT ?area ?osm\n'
            'WHERE\n'
            '{\n'
            # parent "instance of" "country" (country = admin level 0)
            '  ?parent wdt:P31 wd:Q6256.\n'
            # parent "contains administrative territorial entity" area
            '  ?parent wdt:P150 ?area.\n'
            # area "instance of" ["subclass of" "admin level 1"]
            '  ?area wdt:P31 [wdt:P279 wd:Q10864048].\n'
            # area "OSM relation ID" osm
            '  ?area wdt:P402 ?osm.\n'
            # parent not "historical country"
            '  MINUS{ ?parent wdt:P31 wd:Q3024240. }\n'
            '}\n'
        )
        for row in rows:
            area = q_entity_uri(row['area'])
            if area not in remaining_admins:
                continue
            remaining_admins.discard(area)
            if area in osm_cache:
                continue

            osm = literal(row['osm'])
            logger.info("Getting from OSM: %s", area)
            response = requests.get(
                f'https://api.openstreetmap.org/api/0.6/relation/{osm}/full',
                headers={'Accept': 'application/json'},
            )
            response.raise_for_status()
            osm_data = response.json()

            # Read nodes
            nodes = {}
            for element in osm_data['elements']:
                if element['type'] == 'node':
                    nodes[element['id']] = element

            # Read ways that are boundaries to compute the bounding box
            merged = None
            for element in osm_data['elements']:
                if (
                    element['type'] == 'way'
                    and 'tags' in element
                    and element['tags'].get('boundary') == 'administrative'
                ):
                    for node in element['nodes']:
                        node = nodes[node]
                        lat, long = node['lat'], node['lon']
                        if merged is None:
                            merged = (
                                long, long,
                                lat, lat,
                            )
                        else:
                            merged = (
                                min(merged[0], long),
                                max(merged[1], long),
                                min(merged[2], lat),
                                max(merged[3], lat),
                            )

            if merged is not None:
                osm_cache[area] = merged
            else:
                osm_cache[area] = None
    except BaseException:
        with open('.bounds1.pkl', 'wb') as fp:
            pickle.dump(osm_cache, fp)
        raise
    else:
        for area, merged in osm_cache.items():
            if merged is not None:
                writer.writerow([
                    area, merged[0], merged[1], merged[2], merged[3],
                ])
        os.remove('.bounds1.pkl')
        logger.info(
            "Filling from OSM complete, still missing %d",
            len(remaining_admins),
        )


@makes_file('country_names.csv')
def country_names(writer):
    """Get the localized names of countries.
    """
    rows = sparql_query(
        'SELECT ?item ?name ?nameLang\n'
        'WHERE\n'
        '{\n'
        '  ?item wdt:P31 wd:Q6256.\n'  # item "instance of" "country"
        '  ?item wdt:P1448 ?name.\n'  # item "official name" name
        '  BIND(LANG(?name) AS ?nameLang).\n'  # nameLang = LANG(name)
        '  MINUS{ ?item wdt:P31 wd:Q3024240. }\n'  # not "historical country"
        '}\n'
    )

    writer.writerow(['country', 'name', 'name_lang'])
    for row in rows:
        value = q_entity_uri(row['item'])
        name = literal(row['name'])
        name_lang = literal(row['nameLang'])

        writer.writerow([value, name, name_lang])


WIKIDATA_ADMIN_LEVELS = [
    'Q6256',        # "country"
    'Q10864048',    # "first-level administrative country subdivision"
    'Q13220204',    # "second-level administrative country subdivision"
    'Q13221722',    # "third-level administrative country subdivision"
    'Q14757767',    # "fourth-level administrative country subdivision"
    'Q15640612',    # "fifth-level administrative country subdivision"
]


def get_admin_level(level):
    if level == 0:
        rows = sparql_query(
            'SELECT ?country ?countryLabel\n'
            'WHERE\n'
            '{\n'
            '  ?country wdt:P31 wd:Q6256.\n'
            '  SERVICE wikibase:label {\n'
            '    bd:serviceParam wikibase:language "[AUTO_LANGUAGE],en".\n'
            '  }\n'
            '}\n'
        )
        for row in rows:
            yield (
                None,
                q_entity_uri(row['country']),
                literal(row['countryLabel']),
            )
    elif level == 1:
        rows = sparql_query(
            'SELECT ?parent ?area ?areaLabel\n'
            'WHERE\n'
            '{\n'
            # parent "instance of" "country" (country = admin level 0)
            '  ?parent wdt:P31 wd:Q6256.\n'
            # parent "contains administrative territorial entity" area
            '  ?parent wdt:P150 ?area.\n'
            # area "instance of" ["subclass of" "admin level 1"]
            '  ?area wdt:P31 [wdt:P279 wd:Q10864048].\n'
            '  SERVICE wikibase:label {\n'
            '    bd:serviceParam wikibase:language "[AUTO_LANGUAGE],en".\n'
            '  }\n'
            '}\n'
        )
        for row in rows:
            yield (
                q_entity_uri(row['parent']),
                q_entity_uri(row['area']),
                literal(row['areaLabel']),
            )
    else:
        rows = sparql_query(
            'SELECT ?parent ?area ?areaLabel\n'
            'WHERE\n'
            '{{\n'
            # tmp0 "instance of" "country" (country = admin level 0)
            '  ?tmp0 wdt:P31 wd:Q6256.\n'
            # go down 0 to level-2 levels, to the immediate parent
            '{levels}\n'
            '  ?parent wdt:P150 ?area.\n'
            '  ?area wdt:P31 [wdt:P279 wd:{klass}].\n'
            '  SERVICE wikibase:label {{\n'
            '    bd:serviceParam wikibase:language "[AUTO_LANGUAGE],en".\n'
            '  }}\n'
            '}}\n'.format(
                levels='\n'.join(
                    f'?tmp{i} wdt:P150? ?tmp{i+1}.'
                    for i in range(level - 2)
                ),
                klass=WIKIDATA_ADMIN_LEVELS[level]
            )
        )
        for row in rows:
            yield (
                q_entity_uri(row['parent']),
                q_entity_uri(row['area']),
                literal(row['areaLabel']),
            )


@makes_file('areas0.csv')
def areas0(writer):
    """Get the level 0 of areas (countries).
    """
    writer.writerow(['parent', 'admin', 'admin level', 'admin name'])

    for parent, admin, admin_name in get_admin_level(0):
        writer.writerow([parent, admin, 0, admin_name])


@makes_file('areas1.csv')
def areas1(writer):
    """Get one level of administrative areas.
    """
    writer.writerow(['parent', 'admin', 'admin level', 'admin name'])

    for parent, admin, admin_name in get_admin_level(1):
        writer.writerow([parent, admin, 1, admin_name])


@makes_file('areas2.csv')
def areas2(writer):
    """Get two levels of administrative areas.
    """
    writer.writerow(['parent', 'admin', 'admin level', 'admin name'])

    for parent, admin, admin_name in get_admin_level(2):
        writer.writerow([parent, admin, 2, admin_name])


def main():
    logging.basicConfig(level=logging.INFO)
    os.chdir(os.path.dirname(__file__) or '.')

    csv.field_size_limit(sys.maxsize)

    countries()
    geoshapes0()
    geoshapes1()
    bounds0()
    bounds1()
    country_names()
    areas0()
    areas1()
    areas2()


if __name__ == '__main__':
    main()
