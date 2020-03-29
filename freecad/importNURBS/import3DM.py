__title__ = "import3DM"
__author__ = "Keith Sloan (keithsloan52) : Christophe Grellier (Chris_G)"
__license__ = "LGPL 2.1"
__doc__ = "import of 3DM file"

# TODO
# - figure out how colors are managed between objects, materials and layers
# - convert Rhino materials into FC materials ?
# - use real arc of circles for ArcCurves
# - implement Brep face trimming (when available in rhino3dm ...)


import FreeCAD 
import os, io, sys
import FreeCADGui 
import Part
from math import pi

try:
    import rhino3dm as r3
except ModuleNotFoundError:
    FreeCAD.Console.PrintError("You must install rhino3dm first !")
    exit()

#print(dir(r3))

if open.__module__ == '__builtin__':
    pythonopen = open # to distinguish python built-in open function from the one declared here

def open(filename):
    "called when freecad opens a file."
    global doc
    docname = os.path.splitext(os.path.basename(filename))[0]
    doc = FreeCAD.newDocument(docname)
    if filename.lower().endswith('.3dm'):
        process3DM(doc,filename)
    return doc

def insert(filename,docname):
    "called when freecad imports a file"
    global doc
    groupname = os.path.splitext(os.path.basename(filename))[0]
    try:
        doc=FreeCAD.getDocument(docname)
    except NameError:
        doc=FreeCAD.newDocument(docname)
    if filename.lower().endswith('.3dm'):
        process3DM(doc,filename)

def attrs(obj, ignore_private=True):
    """Debugging tool.
    Print all the attributes of an object
    and the result of the no-arguments methods"""
    ignore = ("Encode",)
    for prop in dir(obj):
        if (prop[0:2] == "__") and ignore_private:
            continue
        if prop in ignore:
            continue
        try:
            attr = getattr(obj, prop)
            if "method" in str(attr):
                print("{}: {}".format(prop, attr()))
            else:
                print("{}: {}".format(prop, attr))
        except:
            pass

class Rhino2FC:
    "Converter from Rhino objects to FreeCAD objects"
    def __init__(self):
        pass

    def get_point_and_weight(self, point4d):
        """Input : rhino3dm.Point4d
        Output : FreeCAD.Vector and weight"""
        p = FreeCAD.Vector(point4d.X / point4d.W,
                           point4d.Y / point4d.W,
                           point4d.Z / point4d.W)
        return p, point4d.W

    def get_point(self, point):
        """Input : rhino3dm.Point3d or rhino3dm.Point3f
        Output : FreeCAD.Vector"""
        return FreeCAD.Vector(point.X, point.Y, point.Z)

    def get_color_and_transparency(self, rhino_color):
        """Input : Rhino color (r,g,b,a) in [0,255] range
        Output : FreeCAD color (r,g,b) in [0.0,1.0] range and transparency (int) in [0,100] range"""
        r,g,b,a = [v/255.0 for v in rhino_color]
        return (r,g,b), int((1-a)*100)

    def get_color(self, rhino_color):
        """Input : Rhino color (r,g,b,a) in [0,255] range
        Output : FreeCAD color (r,g,b,a) in [0.0,1.0] range"""
        r,g,b,a = [v/255.0 for v in rhino_color]
        return r,g,b,a

    def get_placement(self, center, normal):
        """Computes a FreeCAD.Placement from Rhino center and normal"""
        z = FreeCAD.Vector(0,0,1)
        fc_norm = self.get_point(normal)
        rot_axis = z.cross(fc_norm)
        angle = z.getAngle(fc_norm)*180.0/pi
        return FreeCAD.Placement(self.get_point(center), rot_axis, angle)

    def get_bspline_curve(self, curve):
        """Input : Rhino rhino3dm.NurbsCurve
        Output : FreeCAD Part.BSplineCurve"""
        pts = []
        weights = []
        for u in range(len(curve.Points)):
            op = curve.Points[u]
            p,w = self.get_point_and_weight(op)
            pts.append(p)
            weights.append(w)
        ku, mu = self.get_FCKnots(curve.Knots)
        periodic = False
        bs = Part.BSplineCurve()
        bs.buildFromPolesMultsKnots(pts, mu, ku, periodic, curve.Degree, weights)
        if mu[0] < (curve.Degree+1):
            bs.setPeriodic()
        return bs

    def get_bspline_surface(self, surf):
        """Input : Rhino rhino3dm.NurbsSurface
        Output : FreeCAD Part.BSplineSurface"""
        pts = []
        weights = []
        for u in range(surf.Points.CountU):
            row = []
            wrow = []
            for v in range(surf.Points.CountV):
                op = surf.Points[u,v]
                p,w = self.get_point_and_weight(op)
                row.append(p)
                wrow.append(w)
            pts.append(row)
            weights.append(wrow)
        ku, mu = self.get_FCKnots(surf.KnotsU)
        kv, mv = self.get_FCKnots(surf.KnotsV)
        uperiodic = False
        vperiodic = False
        bs = Part.BSplineSurface()
        bs.buildFromPolesMultsKnots(pts, mu, mv, ku, kv, uperiodic, vperiodic,
                                    surf.Degree(0), surf.Degree(1), weights)
        if mu[0] < (surf.Degree(0)+1):
            bs.setUPeriodic()
        if mv[0] < (surf.Degree(1)+1):
            bs.setVPeriodic()
        return bs

    def get_FCKnots(self, fknots):
        "Convert Rhino knots sequence into FreeCAD knots and mults"
        k = list(fknots)
        mults = []
        knots = list(set(k))
        knots.sort()
        for kn in knots:
            mults.append(k.count(kn))
        mults[0] += 1
        mults[-1] += 1
        return knots, mults
    

class File3dm:

    def __init__(self, path):
        self.f3dm = r3.File3dm.Read(path)
        self.layers = []
        self.groups = []
        self.materials = []
        self.r2fc = Rhino2FC()

    def parse_objects(self, doc=None):
        if not doc:
            doc = FreeCAD.newDocument("3dm import")
        #part = doc.addObject('App::Part','Part')
        for i in range(len(self.f3dm.Objects)):
            obj_fullname = "{}".format(self.f3dm.Objects[i].Geometry)
            first_split = obj_fullname.split(".")
            second_split = first_split[-1].split(" ")
            print("-----------------\n{}".format(second_split[0]))
            #attrs(self.f3dm.Objects[i].Attributes)
            layer_idx = self.f3dm.Objects[i].Attributes.LayerIndex
            obj = self.import_geometry(doc, self.f3dm.Objects[i].Geometry)
            if obj:
                if layer_idx < len(self.layers):
                    l = self.layers[layer_idx].Group
                    l.append(obj)
                    self.layers[layer_idx].Group = l

    def parse_groups(self, doc=None):
        if not doc:
            doc = FreeCAD.newDocument("3dm import")
        for i in range(len(self.f3dm.Groups)):
            print("\nGroup {}".format(i))
            attrs(self.f3dm.Groups[i])
            self.groups.append(self.f3dm.Groups[i])

    def parse_materials(self, doc=None):
        if not doc:
            doc = FreeCAD.newDocument("3dm import")
        for i in range(len(self.f3dm.Materials)):
            print("\nMaterial {}".format(i))
            m = self.f3dm.Materials[i]
            #attrs(m)
            mat = FreeCAD.Material()
            mat.AmbientColor = self.r2fc.get_color(m.AmbientColor)
            mat.DiffuseColor = self.r2fc.get_color(m.DiffuseColor)
            mat.EmissiveColor = self.r2fc.get_color(m.EmissionColor)
            mat.SpecularColor = self.r2fc.get_color(m.SpecularColor)
            mat.Shininess = m.Shine
            mat.Transparency = m.Transparency
            self.materials.append(mat)
        #if len(self.materials) == 0:
            #self.materials.append(FreeCAD.Material())

    def parse_layers(self, doc=None):
        if not doc:
            doc = FreeCAD.newDocument("3dm import")
        if len(self.f3dm.Layers) > 0:
            import Draft
        for i in range(len(self.f3dm.Layers)):
            print("\nLayer {}".format(i))
            r3l = self.f3dm.Layers[i]
            #attrs(r3l)
            layer = Draft.makeLayer()
            doc.recompute()
            layer.Label = r3l.Name
            mat_idx = r3l.RenderMaterialIndex
            if FreeCAD.GuiUp:
                if mat_idx > 0 and mat_idx < len(self.materials):
                    layer.ViewObject.LineColor = (0.0, 0.0, 0.0, 0.0) #self.r2fc.get_color(self.materials[mat_idx].DiffuseColor)
                    layer.ViewObject.ShapeColor = self.r2fc.get_color(self.materials[mat_idx].DiffuseColor)
                    layer.ViewObject.Transparency = int(self.materials[mat_idx].Transparency)
                else:
                    layer.ViewObject.LineColor = self.r2fc.get_color(r3l.PlotColor)
                    layer.ViewObject.ShapeColor = self.r2fc.get_color(r3l.Color)
                # Prevent black ShapeColor
                if layer.ViewObject.ShapeColor[0:3] == (0.0, 0.0, 0.0):
                    layer.ViewObject.ShapeColor = (1.0, 1.0, 1.0)
            self.layers.append(layer)

    def import_geometry(self, doc, geo):
        if isinstance(geo, r3.Brep):
            print("Brep object")
            print("is solid : {}".format(geo.IsSolid))
            print("is manifold : {}".format(geo.IsManifold))
            print("is surface : {}".format(geo.IsSurface))
            print("has {} faces".format(len(geo.Faces)))
            print("has {} surfaces".format(len(geo.Surfaces)))
            print("has {} edges".format(len(geo.Edges)))
            shapes = []
            for i in range(len(geo.Faces)):
                #print(geo.Faces[i])
                s = self.r2fc.get_bspline_surface(geo.Faces[i].ToNurbsSurface())
                shapes.append(s.toShape())
            com = Part.Compound(shapes)
            obj = doc.addObject("Part::Feature","Faces")
            obj.Shape = com
            return obj
        if isinstance(geo, r3.BezierCurve):
            print("Bezier Curve Object")

        if isinstance(geo, r3.Bitmap):
            print("Bitmap Object")

        if isinstance(geo, r3.Box):
            print("Box Object")
            attrs(geo)

        if isinstance(geo, r3.Circle):
            print("Circle Object")
            obj = doc.addObject("Part::Circle","Circle")
            obj.Radius = geo.Radius
            obj.Placement = self.r2fc.get_placement(geo.Center, geo.Normal)
            return obj

        if isinstance(geo, r3.Cone):
            print("Cone Object")
            attrs(geo)

        if isinstance(geo, r3.Curve):
            print("Curve Object")
            c = self.import_curve(doc, geo)
            return c

        if isinstance(geo, r3.Cylinder):
            print("Cylinder Object")
            attrs(geo)

        if isinstance(geo, r3.Ellipse):
            print("Ellipse Object")
            attrs(geo)

        if isinstance(geo, r3.Mesh):
            print("Mesh Object")
            attrs(geo)
            print("")
            print("Vertices : {}".format(len(geo.Vertices)))
            for i in range(min(len(geo.Vertices), 10)):
                print(geo.Vertices[i])
            print("Faces : {}".format(len(geo.Faces)))
            print("-->Triangles : {}".format(geo.Faces. TriangleCount))
            print("-->Quads : {}".format(geo.Faces.QuadCount))
            for i in range(min(len(geo.Faces), 10)):
                print(geo.Faces[i])
            print("Normals : {}".format(len(geo.Normals)))
            for i in range(min(len(geo.Normals), 10)):
                print(geo.Normals[i])
            
            import Mesh
            geo.Faces.ConvertQuadsToTriangles()
            pts = []
            for i in range(len(geo.Faces)):
                for j in range(3):
                    pts.append([geo.Vertices[geo.Faces[i][j]].X,
                                geo.Vertices[geo.Faces[i][j]].Y,
                                geo.Vertices[geo.Faces[i][j]].Z])
            mesh = Mesh.Mesh(pts)
            obj = doc.addObject("Mesh::Feature","Mesh")
            obj.Mesh = mesh
            return obj

        if isinstance(geo, r3.NurbsSurface):
            print("NurbsSurface Object")
            s = self.r2fc.get_bspline_surface(geo)
            obj = doc.addObject("Part::Feature","NurbsSurface")
            obj.Shape = s.toShape()
            return obj

        if isinstance(geo, r3.Point):
            print("Point Object")
            obj = doc.addObject("Part::Vertex","Point")
            obj.X = geo.Location.X
            obj.Y = geo.Location.Y
            obj.Z = geo.Location.Z
            return obj

        if isinstance(geo, r3.PointCloud):
            print("PointCloud Object")
            obj = doc.addObject("Part::Feature","PointCloud")
            vertexes = []
            for i in range(geo.Count):
                vertexes.append(Part.Vertex(self.r2fc.get_point(geo[i])))
            obj.Shape = Part.Compound(vertexes)
            return obj

        if isinstance(geo, r3.Surface):
            print("Surface Object")
            s = self.r2fc.get_bspline_surface(geo.ToNurbsSurface())
            obj = doc.addObject("Part::Feature","NurbsSurface")
            obj.Shape = s.toShape()
            return obj

    def import_curve(self, doc, geo):
        obj = None
        if isinstance(geo, r3.LineCurve):
            print(">_LineCurve Object")
            #attrs(geo)
            p1 = geo.PointAtStart
            p2 = geo.PointAtEnd
            obj = doc.addObject("Part::Line","Line")
            obj.X1 = p1.X
            obj.Y1 = p1.Y
            obj.Z1 = p1.Z
            obj.X2 = p2.X
            obj.Y2 = p2.Y
            obj.Z2 = p2.Z

        elif isinstance(geo, r3.NurbsCurve):
            print(">_NurbsCurve Object")
            bs = self.r2fc.get_bspline_curve(geo)
            if bs:
                obj = doc.addObject("Part::Spline","NurbsCurve")
                obj.Shape = bs.toShape()

        elif isinstance(geo, r3.ArcCurve):
            print(">_ArcCurve Object")
            #obj = doc.addObject("Part::Circle","ArcCurve")
            #obj.Radius = geo.Radius
            #TODO : Use a real circle
            bs = self.r2fc.get_bspline_curve(geo.ToNurbsCurve())
            if bs:
                obj = doc.addObject("Part::Spline","ArcCurve")
                obj.Shape = bs.toShape()

        return obj

def process3DM(doc, filename) :
    FreeCAD.Console.PrintMessage('Import 3DM file : '+filename+'\n')
    FreeCAD.Console.PrintMessage('Import3DM Version 0.01\n')

    att = ["ApplicationName",
        "ApplicationUrl",
        "ApplicationDetails",
        "CreatedBy",
        "LastEditedBy",
        "Revision"]

    fi = File3dm(filename)
    fi.parse_materials(doc)
    fi.parse_groups(doc)
    fi.parse_layers(doc)
    fi.parse_objects(doc)

    FreeCADGui.SendMsgToActiveView("ViewFit")

    #pathName = os.path.dirname(os.path.normpath(filename))

    FreeCAD.Console.PrintMessage('3DM File Imported\n')

