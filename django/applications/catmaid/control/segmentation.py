import json
import time

from django.conf import settings
from django.http import HttpResponse

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404

from catmaid.models import *
from catmaid.control.authentication import *
from catmaid.control.common import *
from catmaid.transaction import *

try:
    import numpy as np
    import h5py
    from PIL import Image
except ImportError:
    pass

from contextlib import closing
from random import choice
import os
import base64, cStringIO
import time
import sys

try:
    import cairo
    import vtk
    import rsvg
except ImportError:
    pass


import random

try:
    # sys.path.append('/home/ottj/3dbar/lib/pymodules/python2.6')
    from bar.rec.pipeline import barPipeline, VTK_PIPELINE
except ImportError:
    pass

# This file defines constants used to correctly define the metadata for NeuroHDF microcircuit data

VerticesTypeSkeletonRootNode = {
    'name': 'skeleton root',
    'id': 1
}

VerticesTypeSkeletonNode = {
    'name': 'skeleton',
    'id': 2
}

VerticesTypeConnectorNode = {
    'name': 'connector',
    'id': 3
}

ConnectivityNeurite = {
    'name': 'neurite',
    'id': 1
}

ConnectivityPresynaptic = {
    'name': 'presynaptic_to',
    'id': 2
}

ConnectivityPostsynaptic = {
    'name': 'postsynaptic_to',
    'id': 3
}

DrawingTypes = {
    'mitochondria' : {
        'value' : 300,
        'string' : 'mitochondria',
        'color': [50,50,255]
    },
    'membrane' : {
        'value' : 400,
        'string' : 'membrane',
        'color': [150,50,50]
    },
    'soma' : {
        'value' : 500,
        'string' : 'soma',
        'color': [255,255,0]
    },
    'misc' : {
        'value' : 600,
        'string' : 'misc',
        'color': [255,50,50]
    },
    'erasor' : {
        'value' : 700,
        'string' : 'erasor',
        'color': [255,255,255]
    }}

def get_drawing_enum(request, project_id=None, stack_id=None):
    return HttpResponse(json.dumps(DrawingTypes), mimetype="text/json")

def generate_mesh(request, project_id=None, stack_id=None):
    skeleton_id = int(request.POST.get('skeleton_id',-1))

    # retrieve all components for a given skeleton id
    components = Component.objects.filter(
        project = project_id,
        stack = stack_id,
        skeleton_id = skeleton_id
    ).all()

    # retrieve stack information
    stack_info = get_stack_info( project_id, stack_id, request.user )
    resolution=stack_info['resolution']
    dimension=stack_info['dimension']
    translation=stack_info['translation']

    # compute the skeleton bounding box
    #    minX, minY = int(dimension['x']), int(dimension['y'])
    #    maxX, maxY = 0,0
    #    minZ, maxZ = int(dimension['z']), 0
    #    for comp in components:
    #        minX = min(minX, comp.min_x)
    #        minY = min(minY, comp.min_y)
    #        minZ = min(minZ, comp.z)
    #        maxX = max(maxX, comp.max_x)
    #        maxY = max(maxY, comp.max_y)
    #        maxZ = max(maxZ, comp.z)
    #
    #    print 'found bounding box', minX, minY, maxX, maxY, minZ, maxZ

    # create 3d array
    data = np.zeros( (dimension['x'], dimension['y'], dimension['z']), dtype = np.uint8 )

    # for all components, retrieve image and bounding box location
    for comp in components:
        print 'work on component', comp.id,  comp.component_id
        img = extract_as_numpy_array( project_id, stack_id, comp.component_id, comp.z ).T
        # store image in array

        height = comp.max_y - comp.min_y + 1
        width = comp.max_x - comp.min_x + 1
        print 'height, width', height, width
        print 'image shape (should match)', img.shape
        try:
            #indX = comp.min_x - minX
            #indY = comp.min_y - minY
            data[comp.min_y:comp.max_y,comp.min_x:comp.max_x,comp.z] = img
        except:
            pass

            # Load npy volume from given file, set origin and spacing of the volue
    npVolWrapper = VTKStructuredPoints.loadVolumeDS(data, spacing = (resolution['x'],resolution['y'],resolution['z']))

    # Convert npy volume to vtkImageData so vtk can handle it
    vtkNumpyDataImport = dataImporterFromNumpy(npVolWrapper)

    # Load pipeline from the xml file
    pipeline = barPipeline.fromXML('default_pipeline.xml')

    # Code just for exporting the mesh (no visualization at this point)
    mesh =  pipeline[0:-1].execute(vtkNumpyDataImport).GetOutput()

    writer = vtk.vtkPolyDataWriter()
    writer.SetInput(mesh)
    writer.SetFileName('test.vtk')
    writer.SetFileTypeToBinary()
    writer.Write()


    return HttpResponse(json.dumps(True), mimetype="text/json")


def retrieve_components_for_location(project_id, stack_id, x, y, z, limit=10):
    componentIds = {}
    fpath = os.path.join( settings.HDF5_STORAGE_PATH, '{0}_{1}_componenttree.hdf'.format( project_id, stack_id ) )
    with closing(h5py.File(fpath, 'r')) as hfile:

        image_data = hfile['connected_components/'+z+'/pixel_list_ids']
        componentMinX = hfile['connected_components/'+z+'/min_x']
        componentMinY = hfile['connected_components/'+z+'/min_y']
        componentMaxX = hfile['connected_components/'+z+'/max_x']
        componentMaxY = hfile['connected_components/'+z+'/max_y']
        thresholdTable = hfile['connected_components/'+z+'/values']

        length=image_data.len()

        print >> sys.stderr, "extract components ...."
        start = time.time()

        #Merge all data into single array
        #TODO:ID instead of length
        merge=np.dstack((np.arange(length),componentMinX.value,componentMinY.value,componentMaxX.value,componentMaxY.value,thresholdTable.value))
        # FIXME: use np.where instead of merging into a new array
        selectionMinXMaxXMinYMaxY=None

        selectionMinX = merge[merge[...,1]<=x]
        if len(selectionMinX):
            selectionMinXMaxX = selectionMinX[selectionMinX[...,3]>=x]
            if len(selectionMinXMaxX):
                selectionMinXMaxXMinY = selectionMinXMaxX[selectionMinXMaxX[...,2]<=y]
                if len(selectionMinXMaxXMinY):
                    selectionMinXMaxXMinYMaxY = selectionMinXMaxXMinY[selectionMinXMaxXMinY[...,4]>=y]

        delta = time.time() - start
        print >> sys.stderr, "took", delta

        print >> sys.stderr, "create components ...."
        start = time.time()

        if selectionMinXMaxXMinYMaxY is not None:

            idx = np.argsort(selectionMinXMaxXMinYMaxY[:,5])
            limit_counter = 0
            for i in idx:
                if limit_counter >= limit:
                    break
                row = selectionMinXMaxXMinYMaxY[i,:]
                componentPixelStart=hfile['connected_components/'+z+'/begin_indices'].value[row[0]].copy()
                componentPixelEnd=hfile['connected_components/'+z+'/end_indices'].value[row[0]].copy()
                data=hfile['connected_components/'+z+'/pixel_list_0'].value[componentPixelStart:componentPixelEnd].copy()

                # check containment of the pixel in the component
                if not len(np.where((data['x'] == x) & (data['y'] == y))[0]):
                    continue

                componentIds[int(row[0])]={
                    'minX': int(row[1]),
                    'minY': int(row[2]),
                    'maxX': int(row[3]),
                    'maxY': int(row[4]),
                    'threshold': row[5]
                }
                limit_counter += 1

        delta = time.time() - start
        print >> sys.stderr, "took", delta

    return componentIds

def get_component_list_for_point(request, project_id=None, stack_id=None):
    """ Generates a JSON List with all intersecting components for
    a given location
    """
    x = int(request.GET.get('x', '0'))
    y = int(request.GET.get('y', '0'))
    z = str(request.GET.get('z', '0'))
    print x,y,z
    componentIds = retrieve_components_for_location(project_id, stack_id, x, y, z)
    return HttpResponse(json.dumps(componentIds), mimetype="text/json")


def extract_as_numpy_array( project_id, stack_id, id, z):
    """ Extract component to a 2D NumPy array
    """
    fpath=os.path.join( settings.HDF5_STORAGE_PATH, '{0}_{1}_componenttree.hdf'.format( project_id, stack_id ) )
    z = str(z)

    with closing(h5py.File(fpath, 'r')) as hfile:

        componentPixelStart = hfile['connected_components/'+z+'/begin_indices'].value[id].copy()
        componentPixelEnd = hfile['connected_components/'+z+'/end_indices'].value[id].copy()
        data = hfile['connected_components/'+z+'/pixel_list_0'].value[componentPixelStart:componentPixelEnd].copy()
        componentMinX = hfile['connected_components/'+z+'/min_x'].value[id]
        componentMinY = hfile['connected_components/'+z+'/min_y'].value[id]
        componentMaxX = hfile['connected_components/'+z+'/max_x'].value[id]
        componentMaxY = hfile['connected_components/'+z+'/max_y'].value[id]

        height, width = componentMaxY - componentMinY + 1, componentMaxX - componentMinX + 1

        img = np.zeros( (width,height), dtype=np.uint8)
        img[data['x']-componentMinX,data['y']-componentMinY] = 1

    return img

# TODO: use extract_as_numpy_array and apply color transfer function depending on the skeleton_id
def get_component_image(request, project_id=None, stack_id=None):

    id = int(request.GET.get('id', '-1'))
    z=request.GET.get('z', '-1')
    red=request.GET.get('red','255')
    green=request.GET.get('green','255')
    blue=request.GET.get('blue','255')
    alpha=request.GET.get('alpha','255')

    fpath=os.path.join( settings.HDF5_STORAGE_PATH, '{0}_{1}_componenttree.hdf'.format( project_id, stack_id ) )
    with closing(h5py.File(fpath, 'r')) as hfile:

        componentPixelStart=hfile['connected_components/'+z+'/begin_indices'].value[id].copy()
        componentPixelEnd=hfile['connected_components/'+z+'/end_indices'].value[id].copy()

        data=hfile['connected_components/'+z+'/pixel_list_0'].value[componentPixelStart:componentPixelEnd].copy()
        threshold=float(hfile['connected_components/'+z+'/values'].value[id].copy())

        componentMinX=hfile['connected_components/'+z+'/min_x'].value[id].copy()
        componentMinY=hfile['connected_components/'+z+'/min_y'].value[id].copy()
        componentMaxX=hfile['connected_components/'+z+'/max_x'].value[id].copy()
        componentMaxY=hfile['connected_components/'+z+'/max_y'].value[id].copy()

        height=(componentMaxY-componentMinY)+1
        width=(componentMaxX-componentMinX)+1


        img = np.zeros( (width,height,4), dtype=np.uint8)
        img[data['x']-componentMinX,data['y']-componentMinY] = (red,green,blue,alpha) # (red, 0, blue, opacity)
        componentImage = Image.fromarray(np.swapaxes(img,0,1))

        response = HttpResponse(mimetype="image/png")
        componentImage.save(response, "PNG")
        return response

    return None

#TODO: in transaction
@login_required
def get_saved_drawings_by_component_id(request, project_id=None, stack_id=None):
    # parse request
    component_id = int(request.GET['component_id'])
    skeleton_id = int(request.GET['skeleton_id'])
    z = int(request.GET['z'])

    s = get_object_or_404(ClassInstance, pk=skeleton_id)
    stack = get_object_or_404(Stack, pk=stack_id)
    p = get_object_or_404(Project, pk=project_id)

    all_drawings = Drawing.objects.filter(stack=stack,
        project=p,skeleton_id=skeleton_id,
        z = z,component_id=component_id).all()

    drawings={}

    for drawing in all_drawings:
        drawings[int(drawing.id)]=\
            {'id':int(drawing.id),
             'componentId':int(drawing.component_id),
             'minX':int(drawing.min_x),
             'minY':int(drawing.min_y),
             'maxX':int(drawing.max_x),
             'maxY':int(drawing.max_y),
             'type':int(drawing.type),
             'svg':drawing.svg,
             'status':drawing.status,
             'skeletonId':drawing.skeleton_id

        }

    return HttpResponse(json.dumps(drawings), mimetype="text/json")



#TODO: in transaction
@login_required
def get_saved_drawings_by_view(request, project_id=None, stack_id=None):
    # parse request
    z = int(request.GET['z'])

    # field of view
    viewX=int(request.GET['x'])
    viewY=int(request.GET['y'])
    viewHeight=int(request.GET['height'])
    viewWidth=int(request.GET['width'])

    stack = get_object_or_404(Stack, pk=stack_id)
    p = get_object_or_404(Project, pk=project_id)

    # fetch all the components for the given z section
    all_drawings = Drawing.objects.filter(
        project = p,
        stack = stack,
        component_id = None,
        z = z).all()

    drawings={}

    for drawing in all_drawings:
        drawings[int(drawing.id)]=\
            {
            'minX':int(drawing.min_x),
            'minY':int(drawing.min_y),
            'maxX':int(drawing.max_x),
            'maxY':int(drawing.max_y),
            'svg':drawing.svg,
            'status':drawing.status,
            'type':drawing.type,
            'id':drawing.id,
            'componentId':drawing.component_id,
            'skeletonId':drawing.skeleton_id

        }

    return HttpResponse(json.dumps(drawings), mimetype="text/json")

#TODO: in transaction
@login_required
def delete_drawing(request, project_id=None, stack_id=None):
    # parse request
    drawingId=request.GET.get('id',None)
    if not drawingId is None:
        all_drawings = Drawing.objects.filter(id=drawingId).all()
        Drawing.delete(all_drawings[0])

    return HttpResponse(json.dumps(True), mimetype="text/json")


#TODO: in transaction
@login_required
def put_drawing(request, project_id=None, stack_id=None):
    # parse request
    drawing=json.loads(request.POST['drawing'])
    skeleton_id = request.POST.__getitem__('skeleton_id')
    z = int(request.POST['z'])

    # field of view
    viewX=int(request.POST['x'])
    viewY=int(request.POST['y'])
    viewHeight=int(request.POST['height'])
    viewWidth=int(request.POST['width'])

    viewMaxX=viewX+viewWidth
    ViewMaxY=viewY+viewHeight
    skeleton=None


    if not skeleton_id =='null':
        skeleton=int(skeleton_id)

    stack = get_object_or_404(Stack, pk=stack_id)
    p = get_object_or_404(Project, pk=project_id)


    new_drawing = Drawing(
        project = p,
        stack = stack,
        user = request.user,
        skeleton_id = skeleton,
        component_id = drawing['componentId'],
        min_x = drawing['minX'],
        min_y = drawing['minY'],
        max_x = drawing['maxX'],
        max_y = drawing['maxY'],
        z = z,
        svg = drawing['svg'],
        type=drawing['type'],
        status = 1
    )
    new_drawing.save()

    return HttpResponse(json.dumps(new_drawing.id), mimetype="text/json")


#TODO: in transaction
@login_required
def get_saved_components(request, project_id=None, stack_id=None):

    # parse request
    skeleton_id = int(request.GET['skeleton_id'])
    z = int(request.GET['z'])

    s = get_object_or_404(ClassInstance, pk=skeleton_id)
    stack = get_object_or_404(Stack, pk=stack_id)
    p = get_object_or_404(Project, pk=project_id)

    # fetch all the components for the given skeleton and z section
    all_components = Component.objects.filter(stack=stack,
        project=p,skeleton_id=skeleton_id,
        z = z).all()

    componentIds={}

    for compData in all_components:
        componentIds[int(compData.component_id)]=\
            {
            'minX':int(compData.min_x),
            'minY':int(compData.min_y),
            'maxX':int(compData.max_x),
            'maxY':int(compData.max_y),
            'threshold':compData.threshold

        }

    return HttpResponse(json.dumps(componentIds), mimetype="text/json")


#TODO: in transaction; separate out creation of a new component in a function

@login_required
def put_components(request, project_id=None, stack_id=None):

    # parse request
    components=json.loads(request.POST['components'])
    skeleton_id = int(request.POST['skeleton_id'])
    z = int(request.POST['z'])


    # field of view
    viewX=int(request.POST['x'])
    viewY=int(request.POST['y'])
    viewHeight=int(request.POST['height'])
    viewWidth=int(request.POST['width'])

    viewMaxX=viewX+viewWidth
    ViewMaxY=viewY+viewHeight

    s = get_object_or_404(ClassInstance, pk=skeleton_id)
    stack = get_object_or_404(Stack, pk=stack_id)
    p = get_object_or_404(Project, pk=project_id)

    # fetch all the components for the given skeleton and z section
    all_components = Component.objects.filter(
        project = p,
        stack = stack,
        skeleton_id = skeleton_id,
        z = z).all()

    # discard the components out of field of view
    activeComponentIds=[]

    for i in components:

        comp=components[i]
        inDatabase=False
        for compDatabse in all_components:
            if str(compDatabse.component_id)==str(comp['id']):
                inDatabase=True
                activeComponentIds.insert(activeComponentIds.__sizeof__(),comp['id'])
                break
        if inDatabase:
            continue

        new_component = Component(
            project = p,
            stack = stack,
            user = request.user,
            skeleton_id = s.id,
            component_id = comp['id'],
            min_x = comp['minX'],
            min_y = comp['minY'],
            max_x = comp['maxX'],
            max_y = comp['maxY'],
            z = z,
            threshold = comp['threshold'],
            status = 1
        )
        new_component.save()
        activeComponentIds.insert(activeComponentIds.__sizeof__(),comp['id'])

    # delete components that were deselected
    for compDatabase in all_components:
        if not activeComponentIds.count(str(compDatabase.component_id)):
            Component.delete(compDatabase)

    return HttpResponse(json.dumps(True), mimetype="text/json")

@login_required
def initialize_components_for_skeleton(request, project_id=None, stack_id=None):
    skeleton_id = int(request.POST['skeleton_id'])

    # retrieve all treenodes for the given skeleton
    treenodes_qs, labels_qs, labelconnector_qs = get_treenodes_qs( project_id, skeleton_id )
    # retrieve stack information to transform world coordinates to pixel coordinates
    stack_info = get_stack_info( project_id, stack_id, request.user )

    skeleton = get_object_or_404(ClassInstance, pk=skeleton_id)
    stack = get_object_or_404(Stack, pk=stack_id)
    project = get_object_or_404(Project, pk=project_id)

    # retrieve all the components belonging to the skeleton
    all_components = Component.objects.filter(
        project = project,
        stack = stack,
        skeleton_id = skeleton.id
    ).all()
    all_component_ids = [comp.component_id for comp in all_components]

    # TODO: some sanity checks, like missing treenodes in a section

    # for each treenode location
    for tn in treenodes_qs:

        x_pixel = int(tn.location.x / stack_info['resolution']['x'])
        y_pixel = int(tn.location.y / stack_info['resolution']['y'])
        z = str( int(tn.location.z / stack_info['resolution']['z']) )

        # select component with lowest threshold value and that contains the pixel value of the location
        component_ids = retrieve_components_for_location(project_id, stack_id, x_pixel, y_pixel, z, limit = 1)

        if not len(component_ids):
            print >> sys.stderr, 'No component found for treenode id', tn.id
            continue
        elif len(component_ids) == 1:
            print >> sys.stderr, 'Exactly one component found for treenode id', tn.id, component_ids
        else:
            print >> sys.stderr, 'More than one component found for treenode id', tn.id, component_ids
            continue

        component_key, component_value = component_ids.items()[0]

        # check if component already exists for this skeleton in the database
        if component_key in all_component_ids:
            print >> sys.stderr, 'Component with id', component_key, ' exists already in the database. Skip it.'
            continue

        # TODO generate default color for all components based on a map of
        # the skeleton id to color space

        # if not, create it
        new_component = Component(
            project = project,
            stack = stack,
            user = request.user,
            skeleton_id = skeleton.id,
            component_id = component_key,
            min_x = component_value['minX'],
            min_y = component_value['minY'],
            max_x = component_value['maxX'],
            max_y = component_value['maxY'],
            z = z,
            threshold = component_value['threshold'],
            status = 5 # means automatically selected component
        )
        new_component.save()

    return HttpResponse(json.dumps({'status': 'success'}), mimetype="text/json")

import sys

def create_segmentation_file(request, project_id=None, stack_id=None):

    skeleton_id = request.POST.get('skeleton_id', None)

    if skeleton_id != 'null':
        skeleton_id = int(skeleton_id)
    else:
        skeleton_id = None

    create_segmentation_neurohdf_file(request, project_id,stack_id,skeleton_id)

    return HttpResponse(json.dumps(True), mimetype="text/json")


def create_segmentation_neurohdf_file(request, project_id, stack_id,skeleton_id=None):
    filename=os.path.join( settings.HDF5_STORAGE_PATH, '{0}_{1}_segmentation.hdf'.format( project_id, stack_id ) )
    componentTreeFilePath=os.path.join( settings.HDF5_STORAGE_PATH, '{0}_{1}_componenttree.hdf'.format( project_id, stack_id ) )

    with closing(h5py.File(filename, 'w')) as hfile:
        hfile.attrs['neurohdf_version'] = '0.1'
        scaleGroup = hfile.create_group("scale")
        scale_zero = scaleGroup.create_group("0")
        sectionGroup = scale_zero.create_group("section")

        # retrieve stack information to transform world coordinates to pixel coordinates
        stack_info = get_stack_info( project_id, stack_id, request.user )

        width=stack_info['dimension']['x']
        height=stack_info['dimension']['x']

        if not skeleton_id is None:
            skeleton = get_object_or_404(ClassInstance, pk=skeleton_id)

        stack = get_object_or_404(Stack, pk=stack_id)
        project = get_object_or_404(Project, pk=project_id)

        whitelist = range( int(stack_info['dimension']['z']) )
        [whitelist.remove( int(k) ) for k,v in stack_info['broken_slices'].items()]

        for z in whitelist:
            section = sectionGroup.create_group(str(z))

            shape=(height,width)

            componentIdsPixelArray=np.zeros(shape, dtype=np.long)
            skeletonIdsPixelArray=np.zeros(shape, dtype=np.long)
            componentDrawingIdsPixelArray=np.zeros(shape, dtype=np.long)

            if not skeleton_id is None:

                # retrieve all the components belonging to the skeleton
                all_components = Component.objects.filter(
                    project = project,
                    stack = stack,
                    skeleton_id = skeleton.id,
                    z=z
                ).all()
                for comp in all_components:

                    with closing(h5py.File(componentTreeFilePath, 'r')) as componenthfile:
                        componentPixelStart=componenthfile['connected_components/'+str(z)+'/begin_indices'].value[comp.component_id].copy()
                        componentPixelEnd=componenthfile['connected_components/'+str(z)+'/end_indices'].value[comp.component_id].copy()

                        data=componenthfile['connected_components/'+str(z)+'/pixel_list_0'].value[componentPixelStart:componentPixelEnd].copy()
                        skeletonIdsPixelArray[data['y'],data['x']] =comp.skeleton_id
                        componentIdsPixelArray[data['y'],data['x']] =comp.component_id

                #Get all drawings belonging to this skeleton
                all_drawings = Drawing.objects.filter(stack=stack,
                    project=project,
                    z = z, skeleton_id = skeleton.id).exclude(component_id__isnull=True).all()
                for componentDrawing in all_drawings:

                    drawingArray = svg2pixel(componentDrawing,componentDrawing.id)
                    indices=np.where(drawingArray>0)
                    x_index = indices[0]+(componentDrawing.min_x-50)
                    y_index = indices[1]+(componentDrawing.min_y-50)
                    idx = (x_index >= 0) & (x_index < width) & (y_index >= 0) & (y_index < height)
                    componentDrawingIdsPixelArray[y_index[idx],x_index[idx]]=componentDrawing.id

            #store arrays to hdf file
            section.create_dataset("components", data=componentIdsPixelArray, compression='gzip', compression_opts=1)
            section.create_dataset("skeletons", data=skeletonIdsPixelArray, compression='gzip', compression_opts=1)
            section.create_dataset("component_drawings", data=componentDrawingIdsPixelArray, compression='gzip', compression_opts=1)

            #generate arrays
            drawingTypeArrays={}
            for drawingType in DrawingTypes:
                drawingTypeArrays[DrawingTypes[drawingType]['value']]=np.zeros(shape, dtype=np.long)

            #Get all drawings without skeleton id
            all_free_drawings = Drawing.objects.filter(stack=stack,
                project=project,
                z = z).exclude(component_id__isnull=False).all()

            for freeDrawing in all_free_drawings:
                drawingArray = svg2pixel(freeDrawing,freeDrawing.id)
                indices=np.where(drawingArray>0)

                x_index = indices[1]+(freeDrawing.min_x-50)
                y_index = indices[0]+(freeDrawing.min_y-50)
                idx = (x_index >= 0) & (x_index < width) & (y_index >= 0) & (y_index < height)

                #Use number from JS canvas tool enum
                drawingTypeArrays[freeDrawing.type][y_index[idx],x_index[idx]]=freeDrawing.id

            #store arrays to hdf file
            for drawingArrayId in drawingTypeArrays:
                match=None
                for drawingType in DrawingTypes:
                    if DrawingTypes[drawingType]['value']==drawingArrayId:
                        match=drawingType
                        break
                section.create_dataset(match, data=drawingTypeArrays[drawingArrayId], compression='gzip', compression_opts=1)

    return



def svg2pixel(drawing, id, maxwidth=0, maxheight=0):
    #Converts drawings into pixel array. Be careful,50px offset are added to the drawing!!!

    nopos=find_between(drawing.svg,">","transform=")+'transform="translate(50 50)" />'
    data='<svg>'+nopos+'</svg>'

    #data='<svg>'+drawing.svg.replace("L","C")+'</svg>'

    svg = rsvg.Handle(data=data)

    x = width = svg.props.width
    y = height = svg.props.height
    #    print "actual dims are " + str((width, height))
    #    print "converting to " + str((maxwidth, maxheight))
    #
    #yscale = xscale = 1
    #
    #    if (maxheight != 0 and width > maxwidth) or (maxheight != 0 and height > maxheight):
    #        x = maxwidth
    #        y = float(maxwidth)/float(width) * height
    #        print "first resize: " + str((x, y))
    #        if y > maxheight:
    #            y = maxheight
    #            x = float(maxheight)/float(height) * width
    #            print "second resize: " + str((x, y))
    #        xscale = float(x)/svg.props.width
    #        yscale = float(y)/svg.props.height

    #Add frame of 50px due to stroke width
    newWidth=width+100
    newHeight=height+100

    #Color
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, newWidth, newHeight)
    context = cairo.Context(surface)
    #context.scale(xscale, yscale)
    svg.render_cairo(context)
    #surface.write_to_png("svg_cairo_color_"+str(id)+".png")

    #Hack via pilimage, cairo frombuffer to numpy produces errors due to wrong array length!!!
    pilImage = Image.frombuffer('RGBA',(newWidth,newHeight),surface.get_data(),'raw','RGBA',0,1)
    #    pilImage.save("svg_pil_rgb_"+str(id), "PNG")

    pilGray=pilImage.convert('L')
    #    pilGray.save("svg_pil_gray_"+str(id), "PNG")

    return np.array(pilGray)

def find_between( s, first, last ):
    try:
        start = s.index( first ) + len( first )
        end = s.index( last, start )
        return s[start:end]
    except ValueError:
        return ""


def get_segmentation_tile(project_id, stack_id,scale,height,width,x,y,z,type):
    fpath=os.path.join( settings.HDF5_STORAGE_PATH, '{0}_{1}_segmentation.hdf'.format( project_id, stack_id ) )
    with closing(h5py.File(fpath, 'r')) as hfile:

        hdfpath = 'scale/' + str(int(scale)) + '/section/'+ str(z)+'/'+type
        image_data=hfile[hdfpath].value
        data=image_data[y:y+height,x:x+width]

        data[data > 0] = 255
        data = data.astype( np.uint8 )

        pilImage = Image.frombuffer('RGBA',(width,height),data,'raw','L',0,1)

        response = HttpResponse(mimetype="image/png")
        pilImage.save(response, "PNG")
        #pilImage.save('segmentation_tile_'+str(x)+'_'+str(y), "PNG")
        return response


def get_tile(request, project_id=None, stack_id=None):
    scale = float(request.GET.get('scale', '0'))
    height = int(request.GET.get('height', '0'))
    width = int(request.GET.get('width', '0'))
    x = int(request.GET.get('x', '0'))
    y = int(request.GET.get('y', '0'))
    z = int(request.GET.get('z', '0'))
    col = request.GET.get('col', 'y')
    row = request.GET.get('row', 'x')
    file_extension = request.GET.get('file_extension', 'png')
    hdf5_path = request.GET.get('hdf5_path', '/')
    type = request.GET.get('type', 'none')

    if hdf5_path=="segmentation_file":
        return get_segmentation_tile(project_id,stack_id,scale,height,width,x,y,z,type)

    fpath=os.path.join( settings.HDF5_STORAGE_PATH, '{0}_{1}.hdf'.format( project_id, stack_id ) )

    with closing(h5py.File(fpath, 'r')) as hfile:
        #import math
        #zoomlevel = math.log(int(scale), 2)
        hdfpath = hdf5_path + '/scale/' + str(int(scale)) + '/data'
        image_data=hfile[hdfpath].value        #
        # data=image_data[y:y+height,x:x+width,z].copy()
        # without copy, would yield expected string or buffer exception

        # XXX: should directly index into the memmapped hdf5 array
        #print >> sys.stderr, 'hdf5 path', hdfpath, image_data, data,
        # data.shape

        #pilImage = Image.frombuffer('RGBA',(width,height),data,'raw','L',0,1)
        pilImage = Image.frombuffer('RGBA',(width,height),image_data[y:y+height,x:x+width,z].copy(),'raw','L',0,1)
        response = HttpResponse(mimetype="image/png")
        pilImage.save(response, "PNG")
        return response

def put_tile(request, project_id=None, stack_id=None):
    """ Store labels to HDF5 """
    #print >> sys.stderr, 'put tile', request.POST

    scale = float(request.POST.get('scale', '0'))
    height = int(request.POST.get('height', '0'))
    width = int(request.POST.get('width', '0'))
    x = int(request.POST.get('x', '0'))
    y = int(request.POST.get('y', '0'))
    z = int(request.POST.get('z', '0'))
    col = request.POST.get('col', 'y')
    row = request.POST.get('row', 'x')
    image = request.POST.get('image', 'x')

    fpath=os.path.join( settings.HDF5_STORAGE_PATH, '{0}_{1}.hdf'.format( project_id, stack_id ) )
    #print >> sys.stderr, 'fpath', fpath

    with closing(h5py.File(fpath, 'a')) as hfile:
        hdfpath = '/labels/scale/' + str(int(scale)) + '/data'
        #print >> sys.stderr, 'storage', x,y,z,height,width,hdfpath
        #print >> sys.stderr, 'image', base64.decodestring(image)
        image_from_canvas = np.asarray( Image.open( cStringIO.StringIO(base64.decodestring(image)) ) )
        hfile[hdfpath][y:y+height,x:x+width,z] = image_from_canvas[:,:,0]

    return HttpResponse("Image pushed to HDF5.", mimetype="plain/text")

"""
class dataImporterFromNumpy(vtk.vtkImageImport):
    def __init__(self, structVol):
        # For VTK to be able to use the data, it must be stored as a VTK-image. This can be done by the vtkImageImport-class which
        # imports raw data and stores it.
        # The preaviusly created array is converted to a string of chars and imported.
        volExtent = structVol.size
        volSpacing = structVol.spacing
        volOrigin = structVol.origin

        data_string = structVol.vol.tostring('F')
        self.CopyImportVoidPointer(data_string, len(data_string))
        del data_string

        # The type of the newly imported data is set to unsigned char (uint8)
        self.SetDataScalarTypeToUnsignedChar()

        # Because the data that is imported only contains an intensity value (it
        # isnt RGB-coded or someting similar), the importer must be told this is
        # the case.
        self.SetNumberOfScalarComponents(1)

        # honestly dont know the difference between SetDataExtent() and
        # SetWholeExtent() although VTK complains if not both are used.

        self.SetDataExtent (0, volExtent[0]-1, 0, volExtent[1]-1, 0, volExtent[2]-1)
        self.SetWholeExtent(0, volExtent[0]-1, 0, volExtent[1]-1, 0, volExtent[2]-1)
        self.SetDataSpacing(volSpacing[0], volSpacing[1], volSpacing[2])
        self.SetDataOrigin (volOrigin[0],  volOrigin[1],  volOrigin[2])



class VTKStructuredPoints():
    def __init__(self, (nx, ny, nz)):
        self.vol=np.zeros( (nx, ny, nz), dtype=np.uint8 )
        self.size=self.vol.shape

    def setOrigin(self, (x, y, z)):\
        self.origin=(x, y, z)

    def setSpacing(self, (sx, sy, sz)):
        self.spacing=(sx, sy, sz)

    def setSlices(self, slideIndexList, sliceArray):
        self.vol[:, :, slideIndexList] = sliceArray

    def prepareVolume(self, indexholderReference):
        # Obligatory (required by vtk):
        self.vol= np.swapaxes(self.vol, 1,0)

    @classmethod
    def loadVolumeDS(cls, arch, origin = (0,0,0), spacing = (1,1,1)):
        result = cls((1, 1, 1))
        result.vol = arch
        result.size = result.vol.shape
        result.origin = origin
        result.spacing = spacing
        return result

"""