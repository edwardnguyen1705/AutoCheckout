import numpy as np
from pymongo import MongoClient
from collections import namedtuple
import cpsdriver.codec as codec
import GroundTruth as GT
import math
from typing import NamedTuple
import io
from PIL import Image, ImageDraw


_mongoClient = MongoClient('mongodb://localhost:27017')
db = _mongoClient['cps-test-01']
planogramDB = db['planogram']
productsDB = db['products']
plateDB = db['plate_data']
_targetsDB = db['targets']
_frameDB = db['frame_message']

_planogram = None
_productsCache = {}
_positionsPerProduct = {}
_coordinatesPerProduct = {}

# store meta
_gondolasDict = None
_shelvesDict = None
_platesDict = None


def _loadPlanogram():
    num_gondola = 5
    num_shelf = 6
    num_plate = 12
    planogram = np.empty((num_gondola, num_shelf, num_plate), dtype=object)

    for item in planogramDB.find():
        for plate in item['plate_ids']:
            shelf = plate['shelf_id']
            gondola = shelf['gondola_id']
            gondolaID = gondola['id']
            shelfID = shelf['shelf_index']
            plateID = plate['plate_index']

            productID = item['planogram_product_id']['id']
            globalCoordinates = item['global_coordinates']['transform']['translation']
            if productID != '':
                planogram[gondolaID-1][shelfID-1][plateID-1] = productID
                if productID not in _positionsPerProduct:
                    _positionsPerProduct[productID]  = []
                _positionsPerProduct[productID].append((gondolaID, shelfID, plateID))

                # TODO: gondola 5 has rotation
                _coordinatesPerProduct[productID] = globalCoordinates
    
    return  planogram

def _loadProducts():
    return None


def getProductByID(productID):
    if productID in _productsCache:
        return _productsCache[productID]
    else:
        product = codec.Product.from_dict(productsDB.find_one({'product_id.id': productID}))

        productExtended = ProductExtended()
        productExtended.barcode_type = product.product_id.barcode_type
        productExtended.barcode = product.product_id.barcode
        productExtended.name = product.name
        productExtended.thumbnail = product.thumbnail
        productExtended.price = product.price
        productExtended.weight = product.price
        productExtended.positions = getProductPositions(productExtended.barcode)
        # print(productExtended.positions)
        _productsCache[productID] = productExtended
        return productExtended

def getFramesForEvent(event):
    timeBegin = event.triggerBegin
    timeEnd = event.triggerEnd
    frames = {}
    # TODO: date_time different format in test 2
    framesCursor = _frameDB.find({
        'date_time': {
            '$gte': timeBegin,
            '$lt': timeEnd
        }
    })

    for frameDoc in framesCursor:
        cameraID = frameDoc['camera_id']
        if cameraID not in frames:
            frames[cameraID] = frameDoc
        else:
            if frames[cameraID]['date_time'] <= frameDoc['date_time']:
                # pick an earlier frame for this camera
                frames[cameraID] = frameDoc
    
    for frameKey in frames:
        # print("Frame Key (camera ID) is: ", frameKey)
        rgbFrame = codec.DocObjectCodec.decode(frames[frameKey], 'frame_message')
        imageStream = io.BytesIO(rgbFrame.frame)
        im = Image.open(imageStream)
        frames[frameKey] = im

    print("Capture {} camera frames in this event".format(len(frames)))
    return frames

"""
Function to get a frame Image from the database
Input:
    timestamp: double/string
    camera_id: int/string, if camera id is not specified, returns all the image with camera IDs
Output:
    (with camera ID) PIL Image: Image object RGB format
    (without camera ID): dictionary {camera_id: PIL Image}
"""
def getFrameImage(timestamp, camera_id=None):
    if camera_id is not None:
        framesCursor = _frameDB.find({
            'timestamp': float(timestamp),
            'camera_id': int(camera_id)
        })
        # One timestamp should corresponds to only one frame
        if (framesCursor.count() == 0):
            return None
        item = framesCursor[0]
        rgb = codec.DocObjectCodec.decode(doc=item, collection='frame_message')
        imageStream = io.BytesIO(rgb.frame)
        im = Image.open(imageStream)
        return im
    else:
        image_dict = {}
        framesCursor = _frameDB.find({
            'timestamp': float(timestamp),
        })
        if (framesCursor.count() == 0):
            return None
        for item in framesCursor:
            # print("Found image with camera id: ", item['camera_id'])
            camera_id = item['camera_id']
            rgb = codec.DocObjectCodec.decode(doc=item, collection='frame_message')
            imageStream = io.BytesIO(rgb.frame)
            im = Image.open(imageStream)
            image_dict[camera_id] = im
        return image_dict

"""
Function to get lastest targets for an event
Input:
    event
Output:
    List[target]: all the in-store target during this event period
"""
def getTargetsForEvent(event):
    timeBegin = event.triggerBegin
    timeEnd = event.triggerEnd
    targetsCursor = _targetsDB.find({
        'date_time': {
            '$gte': timeBegin,
            '$lt': timeEnd
        }
    })
    # Sort the all targets entry in a timely order
    targetsCursor.sort([('date_time', 1)])

    targets = {}
    for targetDoc in targetsCursor:
        target_list = targetDoc['document']['targets']['targets']
        for target in target_list:
            target_id = target['target_id']['id']
            valid_entrance = target['target_state'] == 'TARGETSTATE_VALID_ENTRANCE'
            x, y, z = target['head']['point']['x'], target['head']['point']['y'], target['head']['point']['z']
            score = target['head']['score']
            coordinate = Coordinates(x, y, z)

            if target_id not in targets:
                # Create new target during this period
                targets[target_id] = Target(target_id, coordinate, score, valid_entrance)
            else:
                # Update existing target
                targets[target_id].update(target_id, coordinate, score, valid_entrance)

    print("Capture {} targets in this event".format(len(targets)))
    return targets

def _findOptimalPlateForEvent(event):
    return 1

def _get3DCoordinatesForPlate(gondola, shelf, plate):
    if _gondolasDict == None:
        _buildDictsFromStoreMeta()
    gondolaMetaKey = str(gondola)
    shelfMetaKey = str(gondola) + '_' + str(shelf)
    plateMetaKey = str(gondola) + '_' + str(shelf) + '_' + str(plate)

    #TODO: rotation values for one special gondola
    absolute3D = Coordinates(0, 0, 0)
    gondolaTranslation = _getTranslation(_gondolasDict[gondolaMetaKey])
    absolute3D.translateBy(gondolaTranslation['x'], gondolaTranslation['y'], gondolaTranslation['z'])

    shelfTranslation = _getTranslation(_shelvesDict[shelfMetaKey])
    absolute3D.translateBy(shelfTranslation['x'], shelfTranslation['y'], shelfTranslation['z'])

    plateTranslation = _getTranslation(_platesDict[plateMetaKey])
    absolute3D.translateBy(plateTranslation['x'], plateTranslation['y'], plateTranslation['z'])

def _getTranslation(meta):
    return meta['coordinates']['transform']['translation']


def _buildDictsFromStoreMeta():
    for gondolaMeta in GT.gondolasMeta:
        _gondolasDict[str(gondolaMeta['id']['id'])] = gondolaMeta

    for shelfMeta in GT.shelvesMeta:
        IDs = shelfMeta['id']
        gondolaID = IDs['gondola_id']['id']
        shelfID = IDs['shelf_index']
        shelfMetaIndexKey = str(gondolaID) + '_' + str(shelfID)
        _shelvesDict[shelfMetaIndexKey] = shelfMetaIndexKey

    for plateMeta in GT.platesMeta:
        IDs = plateMeta['id']
        gondolaID = IDs['shelf_id']['gondola_id']['id']
        shelfID = IDs['shelf_id']['shelf_index']
        plateID = IDs['plate_index']
        plateMetaIndexKey = str(gondolaID) + '_' + str(shelfID) + '_' + str(plateID)
        _platesDict[plateMetaIndexKey] = plateMeta
    

def getProductIDsFromPosition(*argv):
    gondolaIdx = argv[0] - 1
    if len(argv) == 2:
        shelfIdx = argv[1] - 1
        # remove Nones
        products = [product for product in _planogram[gondolaIdx][shelfIdx] if product]
        # deduplication
        products = list(dict.fromkeys(products))
        return products
    if len(argv) == 3:
        shelfIdx = argv[1] - 1
        plateIdx = argv[2] - 1
        return _planogram[gondolaIdx][shelfIdx][plateIdx]

def getProductPosAverage(productID):
    positions = _positionsPerProduct[productID]
    if len(positions) <= 0:
        return None
    middleIndex = math.floor(len(positions) / 2)
    midPos = positions[middleIndex]
    return Position(midPos[0], midPos[1], midPos[2])

def getProductPositions(productID):
    positions = []
    for pos in _positionsPerProduct[productID]:
        positions.append(Position(pos[0], pos[1], pos[2]))
    return positions

def getProductCoordinates(productID):
    coord = _coordinatesPerProduct[productID]
    return Coordinates(coord['x'], coord['y'], coord['z'])

class Position:
    gondola: int
    shelf: int
    plate: int
    def __init__(self, gondola, shelf, plate):
        self.gondola = gondola
        self.shelf = shelf
        self.plate = plate
    
    def __repr__(self):
        return str(self)

    def __str__(self):
        return 'Position(gondola=%d, shelf=%d, plate=%d)' % (self.gondola, self.shelf, self.plate)

class Coordinates: 
    def __init__(self, x, y, z):
        self.x = x
        self.y = y
        self.z = z
    
    def translateBy(self, delta_x, delta_y, delta_z):
        self.x += delta_x
        self.y += delta_y
        self.z += delta_z

    def __repr__(self):
        return str(self)

    def __str__(self):
        return 'Coordinates(%d, %d, %d)' % (self.x, self.y, self.z)

# class Frame:

"""
Class for customer target
Attributes:
    self.head: Coordinates. global coordinate of head position. Usage: Coordinates.x, Coordinates.y, Coordinates.z
    self.id: STRING. Identify of the target.
    self.score: FLOAT. Confidence score of the target existence.
    self.valid_entrance: BOOL. Whether this target is a valid entrance at the store.
"""
class Target:
    def __init__(self, id, Coordinates, score, valid_entrance=True):
        self.head = Coordinates
        self.id = id
        self.score = score
        self.valid_entrance = valid_entrance
    
    def update(self, id, Coordinates, score, valid_entrance=True):
        self.head = Coordinates
        self.id = id
        self.score = score
        self.valid_entrance = valid_entrance
    
    def __str__(self):
        return 'Target(ID: {})'.format(str(self.id))

class ProductExtended():
    barcode_type: str
    barcode: str
    name: str
    thumbnail: str
    price: float
    weight: float
    positions: list
    
    def __repr__(self):
        return str(self)
    
    def __str__(self):
        return 'Product(barcode_type=%s, barcode=%s, name=%s, thumbnail=%s, price=%f, weight=%f, positions=%s)' % (
            self.barcode_type,
            self.barcode,
            self.name,
            self.thumbnail,
            self.price,
            self.weight,
            str(self.positions)
        )
_planogram = _loadPlanogram()

_products = _loadProducts()
