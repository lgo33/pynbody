import pynbody
import numpy as np
import pylab as p
import pickle


def test_images():

    f = pynbody.load("testdata/g15784.lr.01024")
    h = f.halos()
    pynbody.analysis.halo.center(h[1])
    f.physical_units()

    im3d = pynbody.plot.sph.image(
        f.gas, width=20.0, units="m_p cm^-3", noplot=True)
    im2d = pynbody.plot.sph.image(
        f.gas, width=20.0, units="m_p cm^-2", noplot=True)

    compare2d, compare3d = np.load("test_im_2d.npy"), np.load("test_im_3d.npy")

    im_grid = pynbody.sph.to_3d_grid(f.gas,nx=200,x2=20.0)[::50]
    compare_grid = np.load("test_im_grid.npy")

    assert np.log10(im2d / compare2d).abs().mean() < 0.03
    assert np.log10(im3d / compare3d).abs().mean() < 0.03
    assert np.log10(im_grid/compare_grid).abs().mean() < 0.03

    # check rectangular image is OK
    im_rect = pynbody.sph.render_image(f.gas,nx=500,ny=250,x2=10.0).in_units("m_p cm^-3")
    compare_rect = compare3d[125:-125]
    assert np.log10(im_rect/ compare_rect).abs().mean() < 0.03