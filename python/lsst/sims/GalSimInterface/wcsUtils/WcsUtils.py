import numpy as np
from lsst.sims.coordUtils import _pixelCoordsFromRaDec, _raDecFromPixelCoords
from lsst.afw.cameraGeom import TAN_PIXELS, FOCAL_PLANE
import lsst.afw.geom as afwGeom
import lsst.afw.image as afwImage
import lsst.afw.image.utils as afwImageUtils
import lsst.daf.base as dafBase
from lsst.sims.GalSimInterface.wcsUtils import approximateWcs
from lsst.sims.utils import _nativeLonLatFromPointing

__all__ = ["tanWcsFromDetector", "tanSipWcsFromDetector"]


def _getTanPixelBounds(afwDetector, afwCamera):

    tanPixelSystem = afwDetector.makeCameraSys(TAN_PIXELS)
    xPixMin = None
    xPixMax = None
    yPixMin = None
    yPixMax = None
    cornerPointList = afwDetector.getCorners(FOCAL_PLANE)
    for cornerPoint in cornerPointList:
        cameraPoint = afwCamera.transform(afwDetector.makeCameraPoint(cornerPoint, FOCAL_PLANE),
                                          tanPixelSystem).getPoint()

        xx = cameraPoint.getX()
        yy = cameraPoint.getY()
        if xPixMin is None or xx < xPixMin:
            xPixMin = xx
        if xPixMax is None or xx > xPixMax:
            xPixMax = xx
        if yPixMin is None or yy < yPixMin:
            yPixMin = yy
        if yPixMax is None or yy > yPixMax:
            yPixMax = yy

    return xPixMin, xPixMax, yPixMin, yPixMax


def tanWcsFromDetector(afwDetector, afwCamera, obs_metadata, epoch):
    """
    Take an afw.cameraGeom detector and return a WCS which approximates
    the focal plane as perfectly flat (i.e. it ignores optical distortions
    that the telescope may impose on the image)

    @param [in] afwDetector is an instantiation of afw.cameraGeom's Detector
    class which characterizes the detector for which you wish to return th
    WCS

    @param [in] afwCamera is an instantiation of afw.cameraGeom's Camera
    class which characterizes the camera containing afwDetector

    @param [in] obs_metadata is an instantiation of ObservationMetaData
    characterizing the telescope's current pointing

    @param [in] epoch is the epoch in Julian years of the equinox against
    which RA and Dec are measured

    @param [out] tanWcs is an instantiation of afw.image's TanWcs class
    representing the WCS of the detector as if there were no optical
    distortions imposed by the telescope.
    """

    xTanPixMin, xTanPixMax, \
    yTanPixMin, yTanPixMax = _getTanPixelBounds(afwDetector, afwCamera)

    xPixList = []
    yPixList = []
    nameList = []

    # dx and dy are set somewhat heuristically
    # setting them eqal to 0.1(max-min) lead to errors
    # on the order of 0.7 arcsec in the WCS

    dx = 0.5*(xTanPixMax-xTanPixMin)
    dy = 0.5*(yTanPixMax-yTanPixMin)

    for xx in np.arange(xTanPixMin, xTanPixMax+0.5*dx, dx):
        for yy in np.arange(yTanPixMin, yTanPixMax+0.5*dy, dy):
            xPixList.append(xx)
            yPixList.append(yy)
            nameList.append(afwDetector.getName())

    raList, decList = _raDecFromPixelCoords(np.array(xPixList),
                                            np.array(yPixList),
                                            nameList,
                                            camera=afwCamera,
                                            obs_metadata=obs_metadata,
                                            epoch=epoch,
                                            includeDistortion=False)

    crPix1, crPix2 = _pixelCoordsFromRaDec(obs_metadata._pointingRA,
                                           obs_metadata._pointingDec,
                                           chipName=afwDetector.getName(), camera=afwCamera,
                                           obs_metadata=obs_metadata, epoch=epoch,
                                           includeDistortion=False)

    lonList, latList = _nativeLonLatFromPointing(raList, decList,
                                                 obs_metadata._pointingRA,
                                                 obs_metadata._pointingDec)

    # convert from native longitude and latitude to intermediate world coordinates
    # according to equations (12), (13), (54) and (55) of
    #
    # Calabretta and Greisen (2002), A&A 395, p. 1077
    #
    radiusList = 180.0/(np.tan(latList)*np.pi)
    uList = radiusList*np.sin(lonList)
    vList = -radiusList*np.cos(lonList)

    delta_xList = xPixList - crPix1
    delta_yList = yPixList - crPix2

    bVector = np.array([
                       (delta_xList*uList).sum(),
                       (delta_yList*uList).sum(),
                       (delta_xList*vList).sum(),
                       (delta_yList*vList).sum()
                       ])

    offDiag = (delta_yList*delta_xList).sum()
    xsq = np.power(delta_xList, 2).sum()
    ysq = np.power(delta_yList, 2).sum()

    aMatrix = np.array([
                       [xsq, offDiag, 0.0, 0.0],
                       [offDiag, ysq, 0.0, 0.0],
                       [0.0, 0.0, xsq, offDiag],
                       [0.0, 0.0, offDiag, ysq]
                       ])

    coeffs = np.linalg.solve(aMatrix, bVector)

    fitsHeader = dafBase.PropertyList()
    fitsHeader.set("RADESYS", "ICRS")
    fitsHeader.set("EQUINOX", epoch)
    fitsHeader.set("CRVAL1", obs_metadata.pointingRA)
    fitsHeader.set("CRVAL2", obs_metadata.pointingDec)
    fitsHeader.set("CRPIX1", crPix1+1)  # the +1 is because LSST uses 0-indexed images
    fitsHeader.set("CRPIX2", crPix2+1)  # FITS files use 1-indexed images
    fitsHeader.set("CTYPE1", "RA---TAN")
    fitsHeader.set("CTYPE2", "DEC--TAN")
    fitsHeader.setDouble("CD1_1", coeffs[0])
    fitsHeader.setDouble("CD1_2", coeffs[1])
    fitsHeader.setDouble("CD2_1", coeffs[2])
    fitsHeader.setDouble("CD2_2", coeffs[3])

    # 20 March 2017
    # the 'try' block is required by the SWIG stack;
    # the 'except' block is required by the pybind11 stack.
    try:
        tanWcs = afwImage.cast_TanWcs(afwImage.makeWcs(fitsHeader))
    except AttributeError:
        tanWcs = afwImage.makeWcs(fitsHeader)

    return tanWcs


def tanSipWcsFromDetector(afwDetector, afwCamera, obs_metadata, epoch,
                          order=3,
                          skyToleranceArcSec=0.001,
                          pixelTolerance=0.01):
    """
    Take an afw Detector and approximate its pixel-to-(Ra,Dec) transformation
    with a TAN-SIP WCs.

    Definition of the TAN-SIP WCS can be found in Shupe and Hook (2008)
    http://fits.gsfc.nasa.gov/registry/sip/SIP_distortion_v1_0.pdf

    @param [in] afwDetector is an instantiation of afw.cameraGeom's Detector
    class which characterizes the detector for which you wish to return th
    WCS

    @param [in] afwCamera is an instantiation of afw.cameraGeom's Camera
    class which characterizes the camera containing afwDetector

    @param [in] obs_metadata is an instantiation of ObservationMetaData
    characterizing the telescope's current pointing

    @param [in] epoch is the epoch in Julian years of the equinox against
    which RA and Dec are measured

    @param [in] order is the order of the SIP polynomials to be fit to the
    optical distortions (default 3)

    @param [in] skyToleranceArcSec is the maximum allowed error in the fitted
    world coordinates (in arcseconds).  Default 0.001

    @param [in] pixelTolerance is the maximum allowed error in the fitted
    pixel coordinates.  Default 0.02

    @param [out] tanSipWcs is an instantiation of afw.image's TanWcs class
    representing the WCS of the detector with optical distortions parametrized
    by the SIP polynomials.
    """

    bbox = afwDetector.getBBox()

    tanWcs = tanWcsFromDetector(afwDetector, afwCamera, obs_metadata, epoch)

    mockExposure = afwImage.ExposureF(bbox.getMaxX(), bbox.getMaxY())
    mockExposure.setWcs(tanWcs)
    mockExposure.setDetector(afwDetector)

    distortedWcs = afwImageUtils.getDistortedWcs(mockExposure.getInfo())
    tanSipWcs = approximateWcs(distortedWcs, bbox,
                               order=order,
                               skyTolerance=skyToleranceArcSec*afwGeom.arcseconds,
                               pixelTolerance=pixelTolerance,
                               detector=afwDetector,
                               camera=afwCamera,
                               obs_metadata=obs_metadata)

    return tanSipWcs

