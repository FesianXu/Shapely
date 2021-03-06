"""Support for various GEOS geometry operations
"""

from ctypes import byref, c_void_p, c_double

from shapely.prepared import prep
from shapely.geos import lgeos
from shapely.geometry.base import geom_factory, BaseGeometry, BaseMultipartGeometry
from shapely.geometry import asShape, asLineString, asMultiLineString, Point, MultiPoint,\
                             LineString, MultiLineString, Polygon, GeometryCollection
from shapely.geometry.polygon import orient as orient_
from shapely.algorithms.polylabel import polylabel


__all__ = ['cascaded_union', 'linemerge', 'operator', 'polygonize',
           'polygonize_full', 'transform', 'unary_union', 'triangulate',
           'split', 'section']


class CollectionOperator(object):

    def shapeup(self, ob):
        if isinstance(ob, BaseGeometry):
            return ob
        else:
            try:
                return asShape(ob)
            except ValueError:
                return asLineString(ob)

    def polygonize(self, lines):
        """Creates polygons from a source of lines

        The source may be a MultiLineString, a sequence of LineString objects,
        or a sequence of objects than can be adapted to LineStrings.
        """
        source = getattr(lines, 'geoms', None) or lines
        try:
            source = iter(source)
        except TypeError:
            source = [source]
        finally:
            obs = [self.shapeup(l) for l in source]
        geom_array_type = c_void_p * len(obs)
        geom_array = geom_array_type()
        for i, line in enumerate(obs):
            geom_array[i] = line._geom
        product = lgeos.GEOSPolygonize(byref(geom_array), len(obs))
        collection = geom_factory(product)
        for g in collection.geoms:
            clone = lgeos.GEOSGeom_clone(g._geom)
            g = geom_factory(clone)
            g._other_owned = False
            yield g

    def polygonize_full(self, lines):
        """Creates polygons from a source of lines, returning the polygons
        and leftover geometries.

        The source may be a MultiLineString, a sequence of LineString objects,
        or a sequence of objects than can be adapted to LineStrings.

        Returns a tuple of objects: (polygons, dangles, cut edges, invalid ring
        lines). Each are a geometry collection.

        Dangles are edges which have one or both ends which are not incident on
        another edge endpoint. Cut edges are connected at both ends but do not
        form part of polygon. Invalid ring lines form rings which are invalid
        (bowties, etc).
        """
        source = getattr(lines, 'geoms', None) or lines
        try:
            source = iter(source)
        except TypeError:
            source = [source]
        finally:
            obs = [self.shapeup(l) for l in source]
        L = len(obs)
        subs = (c_void_p * L)()
        for i, g in enumerate(obs):
            subs[i] = g._geom
        collection = lgeos.GEOSGeom_createCollection(5, subs, L)
        dangles = c_void_p()
        cuts = c_void_p()
        invalids = c_void_p()
        product = lgeos.GEOSPolygonize_full(
            collection, byref(dangles), byref(cuts), byref(invalids))
        return (
            geom_factory(product),
            geom_factory(dangles),
            geom_factory(cuts),
            geom_factory(invalids)
            )

    def linemerge(self, lines):
        """Merges all connected lines from a source

        The source may be a MultiLineString, a sequence of LineString objects,
        or a sequence of objects than can be adapted to LineStrings.  Returns a
        LineString or MultiLineString when lines are not contiguous.
        """
        source = None
        if hasattr(lines, 'type') and lines.type == 'MultiLineString':
            source = lines
        elif hasattr(lines, '__iter__'):
            try:
                source = asMultiLineString([ls.coords for ls in lines])
            except AttributeError:
                source = asMultiLineString(lines)
        if source is None:
            raise ValueError("Cannot linemerge %s" % lines)
        result = lgeos.GEOSLineMerge(source._geom)
        return geom_factory(result)

    def cascaded_union(self, geoms):
        """Returns the union of a sequence of geometries

        This method was superseded by :meth:`unary_union`.
        """
        try:
            L = len(geoms)
        except TypeError:
            geoms = [geoms]
            L = 1
        subs = (c_void_p * L)()
        for i, g in enumerate(geoms):
            subs[i] = g._geom
        collection = lgeos.GEOSGeom_createCollection(6, subs, L)
        return geom_factory(lgeos.methods['cascaded_union'](collection))

    def unary_union(self, geoms):
        """Returns the union of a sequence of geometries

        This method replaces :meth:`cascaded_union` as the
        prefered method for dissolving many polygons.
        """
        try:
            L = len(geoms)
        except TypeError:
            geoms = [geoms]
            L = 1
        subs = (c_void_p * L)()
        for i, g in enumerate(geoms):
            subs[i] = g._geom
        collection = lgeos.GEOSGeom_createCollection(6, subs, L)
        return geom_factory(lgeos.methods['unary_union'](collection))

operator = CollectionOperator()
polygonize = operator.polygonize
polygonize_full = operator.polygonize_full
linemerge = operator.linemerge
cascaded_union = operator.cascaded_union
unary_union = operator.unary_union


def triangulate(geom, tolerance=0.0, edges=False):
    """Creates the Delaunay triangulation and returns a list of geometries

    The source may be any geometry type. All vertices of the geometry will be
    used as the points of the triangulation.

    From the GEOS documentation:
    tolerance is the snapping tolerance used to improve the robustness of
    the triangulation computation. A tolerance of 0.0 specifies that no
    snapping will take place.

    If edges is False, a list of Polygons (triangles) will be returned.
    Otherwise the list of LineString edges is returned.

    """
    func = lgeos.methods['delaunay_triangulation']
    gc = geom_factory(func(geom._geom, tolerance, int(edges)))
    return [g for g in gc.geoms]

class ValidateOp(object):
    def __call__(self, this):
        return lgeos.GEOSisValidReason(this._geom)

validate = ValidateOp()


def transform(func, geom):
    """Applies `func` to all coordinates of `geom` and returns a new
    geometry of the same type from the transformed coordinates.

    `func` maps x, y, and optionally z to output xp, yp, zp. The input
    parameters may iterable types like lists or arrays or single values.
    The output shall be of the same type. Scalars in, scalars out.
    Lists in, lists out.

    For example, here is an identity function applicable to both types
    of input.

      def id_func(x, y, z=None):
          return tuple(filter(None, [x, y, z]))

      g2 = transform(id_func, g1)

    A partially applied transform function from pyproj satisfies the
    requirements for `func`.

      from functools import partial
      import pyproj

      project = partial(
          pyproj.transform,
          pyproj.Proj(init='epsg:4326'),
          pyproj.Proj(init='epsg:26913'))

      g2 = transform(project, g1)

    Lambda expressions such as the one in

      g2 = transform(lambda x, y, z=None: (x+1.0, y+1.0), g1)

    also satisfy the requirements for `func`.
    """
    if geom.is_empty:
        return geom
    if geom.type in ('Point', 'LineString', 'LinearRing', 'Polygon'):

        # First we try to apply func to x, y, z sequences. When func is
        # optimized for sequences, this is the fastest, though zipping
        # the results up to go back into the geometry constructors adds
        # extra cost.
        try:
            if geom.type in ('Point', 'LineString', 'LinearRing'):
                return type(geom)(zip(*func(*zip(*geom.coords))))
            elif geom.type == 'Polygon':
                shell = type(geom.exterior)(
                    zip(*func(*zip(*geom.exterior.coords))))
                holes = list(type(ring)(zip(*func(*zip(*ring.coords))))
                             for ring in geom.interiors)
                return type(geom)(shell, holes)

        # A func that assumes x, y, z are single values will likely raise a
        # TypeError, in which case we'll try again.
        except TypeError:
            if geom.type in ('Point', 'LineString', 'LinearRing'):
                return type(geom)([func(*c) for c in geom.coords])
            elif geom.type == 'Polygon':
                shell = type(geom.exterior)(
                    [func(*c) for c in geom.exterior.coords])
                holes = list(type(ring)([func(*c) for c in ring.coords])
                             for ring in geom.interiors)
                return type(geom)(shell, holes)

    elif geom.type.startswith('Multi') or geom.type == 'GeometryCollection':
        return type(geom)([transform(func, part) for part in geom.geoms])
    else:
        raise ValueError('Type %r not recognized' % geom.type)


def nearest_points(g1, g2):
    """Returns the calculated nearest points in the input geometries

    The points are returned in the same order as the input geometries.
    """
    seq = lgeos.methods['nearest_points'](g1._geom, g2._geom)
    if seq is None:
        if g1.is_empty:
            raise ValueError('The first input geometry is empty')
        else:
            raise ValueError('The second input geometry is empty')
    x1 = c_double()
    y1 = c_double()
    x2 = c_double()
    y2 = c_double()
    lgeos.GEOSCoordSeq_getX(seq, 0, byref(x1))
    lgeos.GEOSCoordSeq_getY(seq, 0, byref(y1))
    lgeos.GEOSCoordSeq_getX(seq, 1, byref(x2))
    lgeos.GEOSCoordSeq_getY(seq, 1, byref(y2))
    p1 = Point(x1.value, y1.value)
    p2 = Point(x2.value, y2.value)
    return (p1, p2)

def snap(g1, g2, tolerance):
    """Snap one geometry to another with a given tolerance

    Vertices of the first geometry are snapped to vertices of the second
    geometry. The resulting snapped geometry is returned. The input geometries
    are not modified.

    Parameters
    ----------
    g1 : geometry
        The first geometry
    g2 : geometry
        The second geometry
    tolerence : float
        The snapping tolerance

    Example
    -------
    >>> square = Polygon([(1,1), (2, 1), (2, 2), (1, 2), (1, 1)])
    >>> line = LineString([(0,0), (0.8, 0.8), (1.8, 0.95), (2.6, 0.5)])
    >>> result = snap(line, square, 0.5)
    >>> result.wkt
    'LINESTRING (0 0, 1 1, 2 1, 2.6 0.5)'
    """
    return(geom_factory(lgeos.methods['snap'](g1._geom, g2._geom, tolerance)))

def shared_paths(g1, g2):
    """Find paths shared between the two given lineal geometries

    Returns a GeometryCollection with two elements:
     - First element is a MultiLineString containing shared paths with the
       same direction for both inputs.
     - Second element is a MultiLineString containing shared paths with the
       opposite direction for the two inputs.

    Parameters
    ----------
    g1 : geometry
        The first geometry
    g2 : geometry
        The second geometry
    """
    if not isinstance(g1, LineString):
        raise TypeError("First geometry must be a LineString")
    if not isinstance(g2, LineString):
        raise TypeError("Second geometry must be a LineString")
    return(geom_factory(lgeos.methods['shared_paths'](g1._geom, g2._geom)))


class SplitOp(object):

    @staticmethod
    def _split_polygon_with_line(poly, splitter):
        """Split a Polygon with a LineString"""

        assert(isinstance(poly, Polygon))
        assert(isinstance(splitter, LineString))

        union = poly.boundary.union(splitter)

        # greatly improves split performance for big geometries with many
        # holes (the following contains checks) with minimal overhead
        # for common cases
        poly = prep(poly)

        # some polygonized geometries may be holes, we do not want them
        # that's why we test if the original polygon (poly) contains
        # an inner point of polygonized geometry (pg)
        return [pg for pg in polygonize(union) if poly.contains(pg.representative_point())]

    @staticmethod
    def _split_line_with_line(line, splitter):
        """Split a LineString with another (Multi)LineString or (Multi)Polygon"""

        # if splitter is a polygon, pick it's boundary
        if splitter.type in ('Polygon', 'MultiPolygon'):
            splitter = splitter.boundary

        assert(isinstance(line, LineString))
        assert(isinstance(splitter, LineString) or isinstance(splitter, MultiLineString))

        if splitter.crosses(line):
            # The lines cross --> return multilinestring from the split
            return line.difference(splitter)
        elif splitter.relate_pattern(line, '1********'):
            # The lines overlap at some segment (linear intersection of interiors)
            raise ValueError('Input geometry segment overlaps with the splitter.')
        else:
            # The lines do not cross --> return collection with identity line
            return [line]

    @staticmethod
    def _split_line_with_point(line, splitter):
        """Split a LineString with a Point"""

        assert(isinstance(line, LineString))
        assert(isinstance(splitter, Point))

        # check if point is in the interior of the line
        if not line.relate_pattern(splitter, '0********'):
            # point not on line interior --> return collection with single identity line
            # (REASONING: Returning a list with the input line reference and creating a
            # GeometryCollection at the general split function prevents unnecessary copying
            # of linestrings in multipoint splitting function)
            return [line]
        elif line.coords[0] == splitter.coords[0]:
            # if line is a closed ring the previous test doesn't behave as desired
            return [line]

        # point is on line, get the distance from the first point on line
        distance_on_line = line.project(splitter)
        coords = list(line.coords)
        # split the line at the point and create two new lines
        current_position = 0.0
        for i in range(len(coords)-1):
            point1 = coords[i]
            point2 = coords[i+1]
            dx = point1[0] - point2[0]
            dy = point1[1] - point2[1]
            segment_length = (dx ** 2 + dy ** 2) ** 0.5
            current_position += segment_length
            if distance_on_line == current_position:
                # splitter is exactly on a vertex
                return [
                    LineString(coords[:i+2]),
                    LineString(coords[i+1:])
                ]
            elif distance_on_line < current_position:
                # splitter is between two vertices
                return [
                    LineString(coords[:i+1] + [splitter.coords[0]]),
                    LineString([splitter.coords[0]] + coords[i+1:])
                ]
        return [line]


    @staticmethod
    def _split_line_with_multipoint(line, splitter):
        """Split a LineString with a MultiPoint"""

        assert(isinstance(line, LineString))
        assert(isinstance(splitter, MultiPoint))

        chunks = [line]
        for pt in splitter.geoms:
            new_chunks = []
            for chunk in filter(lambda x: not x.is_empty, chunks):
                # add the newly split 2 lines or the same line if not split
                new_chunks.extend(SplitOp._split_line_with_point(chunk, pt))
            chunks = new_chunks

        return chunks

    @staticmethod
    def split(geom, splitter):
        """
        Splits a geometry by another geometry and returns a collection of geometries. This function is the theoretical
        opposite of the union of the split geometry parts. If the splitter does not split the geometry, a collection
        with a single geometry equal to the input geometry is returned.
        The function supports:
          - Splitting a (Multi)LineString by a (Multi)Point or (Multi)LineString or (Multi)Polygon
          - Splitting a (Multi)Polygon by a LineString

        It may be convenient to snap the splitter with low tolerance to the geometry. For example in the case
        of splitting a line by a point, the point must be exactly on the line, for the line to be correctly split.
        When splitting a line by a polygon, the boundary of the polygon is used for the operation.
        When splitting a line by another line, a ValueError is raised if the two overlap at some segment.

        Parameters
        ----------
        geom : geometry
            The geometry to be split
        splitter : geometry
            The geometry that will split the input geom

        Example
        -------
        >>> pt = Point((1, 1))
        >>> line = LineString([(0,0), (2,2)])
        >>> result = split(line, pt)
        >>> result.wkt
        'GEOMETRYCOLLECTION (LINESTRING (0 0, 1 1), LINESTRING (1 1, 2 2))'
        """

        if geom.type in ('MultiLineString', 'MultiPolygon'):
             return GeometryCollection([i for part in geom.geoms for i in SplitOp.split(part, splitter).geoms])

        elif geom.type == 'LineString':
            if splitter.type in ('LineString', 'MultiLineString', 'Polygon', 'MultiPolygon'):
                split_func = SplitOp._split_line_with_line
            elif splitter.type in ('Point'):
                split_func = SplitOp._split_line_with_point
            elif splitter.type in ('MultiPoint'):
                split_func =  SplitOp._split_line_with_multipoint
            else:
                raise ValueError("Splitting a LineString with a %s is not supported" % splitter.type)

        elif geom.type == 'Polygon':
            if splitter.type == 'LineString':
                split_func = SplitOp._split_polygon_with_line
            else:
                raise ValueError("Splitting a Polygon with a %s is not supported" % splitter.type)

        else:
            raise ValueError("Splitting %s geometry is not supported" % geom.type)

        return GeometryCollection(split_func(geom, splitter))

split = SplitOp.split


def substring(geom, start_dist, end_dist, normalized=False):
    """Return a line segment between specified distances along a linear geometry.

    Negative distance values are taken as measured in the reverse
    direction from the end of the geometry. Out-of-range index
    values are handled by clamping them to the valid range of values.
    If the start distances equals the end distance, a point is being returned.
    If the normalized arg is True, the distance will be interpreted as a
    fraction of the geometry's length.
    """

    assert(isinstance(geom, LineString))

    # Filter out cases in which to return a point
    if start_dist == end_dist:
        return geom.interpolate(start_dist, normalized)
    elif not normalized and start_dist >= geom.length and end_dist >= geom.length:
        return geom.interpolate(geom.length, normalized)
    elif not normalized and -start_dist >= geom.length and -end_dist >= geom.length:
        return geom.interpolate(0, normalized)
    elif normalized and start_dist >= 1 and end_dist >= 1:
        return geom.interpolate(1, normalized)
    elif normalized and -start_dist >= 1 and -end_dist >= 1:
        return geom.interpolate(0, normalized)

    start_point = geom.interpolate(start_dist, normalized)
    end_point = geom.interpolate(end_dist, normalized)

    min_dist = min(start_dist, end_dist)
    max_dist = max(start_dist, end_dist)
    if normalized:
        min_dist *= geom.length
        max_dist *= geom.length

    if start_dist < end_dist:
        vertex_list = [(start_point.x, start_point.y)]
    else:
        vertex_list = [(end_point.x, end_point.y)]

    coords = list(geom.coords)
    current_distance = 0
    for p1, p2 in zip(coords, coords[1:]):
        if min_dist < current_distance < max_dist:
            vertex_list.append(p1)
        elif current_distance >= max_dist:
            break

        current_distance += ((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2) ** 0.5

    if start_dist < end_dist:
        vertex_list.append((end_point.x, end_point.y))
    else:
        vertex_list.append((start_point.x, start_point.y))
        # reverse direction result
        vertex_list = reversed(vertex_list)

    return LineString(vertex_list)


def clip_by_rect(geom, xmin, ymin, xmax, ymax):
    """Returns the portion of a geometry within a rectangle

    The geometry is clipped in a fast but possibly dirty way. The output is
    not guaranteed to be valid. No exceptions will be raised for topological
    errors.

    Parameters
    ----------
    geom : geometry
        The geometry to be clipped
    xmin : float
        Minimum x value of the rectangle
    ymin : float
        Minimum y value of the rectangle
    xmax : float
        Maximum x value of the rectangle
    ymax : float
        Maximum y value of the rectangle

    Notes
    -----
    Requires GEOS >= 3.5.0
    New in 1.7.
    """
    if geom.is_empty:
        return geom
    result = geom_factory(lgeos.methods['clip_by_rect'](geom._geom, xmin, ymin, xmax, ymax))
    return result


def orient(geom, sign=1.0):
    """A properly oriented copy of the given geometry.

    The signed area of the result will have the given sign. A sign of
    1.0 means that the coordinates of the product's exterior rings will
    be oriented counter-clockwise.

    Parameters
    ----------
    geom : Geometry
        The original geometry. May be a Polygon, MultiPolygon, or
        GeometryCollection.
    sign : float, optional.
        The sign of the result's signed area.

    Returns
    -------
    Geometry

    """
    if isinstance(geom, BaseMultipartGeometry):
        return geom.__class__(
            list(
                map(
                    lambda geom: orient(geom, sign),
                    geom.geoms,
                )
            )
        )
    if isinstance(geom, (Polygon,)):
        return orient_(geom, sign)
    return geom
