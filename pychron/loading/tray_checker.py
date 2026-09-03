# ===============================================================================
# Copyright 2022 Jake Ross
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ===============================================================================
import base64
import io

# ============= enthought library imports =======================
import os
import time
from math import ceil

import joblib
import requests
from PIL import Image
from numpy import hstack, column_stack, savetxt, savez, save, load, asarray, concatenate
from sklearn import metrics, svm
from sklearn.model_selection import train_test_split
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from traits.api import Any, Instance, HasTraits, Dict, Enum, Event, Button
from traitsui.api import View, UItem, Item, VGroup, HGroup

# ============= standard library imports ========================
# ============= local library imports  ==========================
from pychron.core.helpers.logger_setup import logging_setup
from pychron.core.helpers.traitsui_shortcuts import okcancel_view
from pychron.core.ui.image_editor import ImageEditor
from pychron.core.ui.thread import Thread
from pychron.core.ui.gui import invoke_in_main_thread
from pychron.image.cv_wrapper import get_size, crop
from pychron.image.standalone_image import FrameImage
from pychron.loading.traydb import LABEL_MAP
from pychron.loggable import Loggable
from pychron.mv.machine_vision_manager import MachineVisionManager
from pychron.paths import paths


class TrayChecker(MachineVisionManager):
    positions = Dict
    display_image = Instance(FrameImage, ())
    refresh_image = Event
    stop_button = Button("Stop")
    good_button = Button("Good")
    empty_button = Button("Empty")
    multigrain_button = Button("MultiGrain")
    contaminant_button = Button("Contaminant")

    post_move_delay = 0.125
    post_check_delay = 0.125

    # traydb = Instance(TrayDB)

    _alive = False
    _active_frame = None
    _samples = None
    _labels = None
    _active_positions = None
    _active_position = None
    _thread = None

    def __init__(self, loading_manager, dbpath=None, *args, **kw):
        super(TrayChecker, self).__init__(*args, **kw)
        if loading_manager:
            self._loading_manager = loading_manager
            self.video = loading_manager.stage_manager.video


    def stop(self):
        self.debug("stop fired")
        self._alive = False

    def scan(self, classify_now=False):
        if classify_now:
            self._samples = []
            self._labels = []
            self._active_positions = (
                self._loading_manager.stage_manager.stage_map.all_holes()
            )

            buttons = HGroup(
                UItem("stop_button"),
                UItem("good_button"),
                UItem("empty_button"),
                UItem("multigrain_button"),
                UItem("contaminant_button"),
            )
            v = okcancel_view(
                buttons,
                UItem(
                    "object.display_image.source_frame",
                    width=640 * 1.25,
                    height=480 * 1.25,
                    editor=ImageEditor(refresh="object.display_image.refresh_needed"),
                ),
            )

            # go to first hole
            self._visit_next_position()

            info = self.edit_traits(v)
        else:
            pipe = None
            info = self.edit_traits(
                view=View(
                    HGroup(UItem("stop_button")),
                    UItem(
                        "object.display_image.source_frame",
                        width=640,
                        height=480,
                        editor=ImageEditor(
                            refresh="object.display_image.refresh_needed"
                        ),
                    ),
                )
            )

            self._alive = True
            self._thread = Thread(target=self._check, args=(info,))
            self._thread.start()

    def _visit_next_position(self):
        try:
            hole = next(self._active_positions)
        except StopIteration:
            self.information_dialog("Classification Complete")
            return

        trayname = self._loading_manager.stage_manager.stage_map.name
        traypath = os.path.join(paths.snapshot_dir, trayname)
        if not os.path.isdir(traypath):
            os.mkdir(traypath)

        pos = hole.id
        self._loading_manager.goto(pos, block=True)
        time.sleep(self.post_move_delay)
        frame = self.new_image_frame(pos)

        self._loading_manager.stage_manager.snapshot(
            name=os.path.join(traypath, "{}.tc".format(pos)),
            render_canvas=False,
            inform=False,
        )
        # guess the label for this image
        possible_label = self._classify(frame)
        self.debug(f"possible label={possible_label}")

        self.display_image.clear()
        self.display_image.source_frame = frame
        # self.display_image.tile(frame)
        # self.display_image.tilify()
        self.display_image.refresh_needed = True

    def _scan(self, pipe, info):
        trayname = self._loading_manager.stage_manager.stage_map.name
        # traypath = os.path.join(paths.snapshot_dir, trayname)
        # if not os.path.isdir(traypath):
        #     os.mkdir(traypath)

        for hole in self._loading_manager.stage_manager.stage_map.all_holes():
            if not self._alive:
                self.debug("exiting check loop")
                break

            pos = hole.id
            # for pos in self._loading_manager.positions:
            self._loading_manager.goto(pos, block=True)
            time.sleep(self.post_move_delay)
            frame = self.new_image_frame(pos)

            self._add_unlabeled_image(pos, frame)

            self.display_image.clear()
            self.display_image.tile(frame)
            self.display_image.tilify()
            self.display_image.refresh_needed = True
            time.sleep(self.post_check_delay)

        info.dispose()
        self.information_dialog(f"Scan of {trayname} complete")

    def _check(self, info):
        for hole in self._loading_manager.stage_manager.stage_map.all_holes():
            if not self._alive:
                self.debug("exiting check loop")
                break

            pos = hole.id
            self._loading_manager.goto(pos, block=True)
            time.sleep(self.post_move_delay)

            self._check_position(pos)

            time.sleep(self.post_check_delay)

        invoke_in_main_thread(info.dispose)

    def test_ml(self):
        clf = self._get_classifier()
        names, samples, labels = self._get_sample_labels()

        idx = 50
        print(names[idx])
        print(clf.predict(samples[idx].reshape(1, -1)))

    def train_ml(self):
        names, samples, labels = self._get_sample_labels()
        print(samples.shape, labels.shape)
        # labels = randint(0,4, size=labels.size)
        use_nn = False
        if use_nn:
            clf = MLPClassifier(hidden_layer_sizes=(5, 2), random_state=1)
        else:
            clf = svm.SVC(gamma=0.001)

        x_train, x_test, y_train, y_test = train_test_split(
            samples, labels, random_state=42
        )
        pipe = make_pipeline(StandardScaler(), clf)
        pipe.fit(x_train, y_train)  # apply scaling on training data

        tp = os.path.join(paths.loading_dir, "tray.clf.joblib")
        joblib.dump(pipe, tp)

        score = pipe.score(x_test, y_test)

        predicted = pipe.predict(x_test)
        self.debug(
            f"Classification report for classifier {clf}:\n"
            f"{metrics.classification_report(y_test, predicted)}\n"
        )
        self.info("training score={}".format(score))

    def new_image_frame(self, pos):
        frame = super(TrayChecker, self).new_image_frame(force=True)
        frame = self._preprocess(frame)
        frame = self._crop(frame, pos=pos)
        self._active_frame = frame
        self._active_position = pos

        return frame

    def _preprocess(self, frame, gamma=2):
        # frame = grayspace(frame)
        # if gamma:
        #     frame = adjust_gamma(frame, gamma)
        return frame

    def _get_classifier(self):
        tp = os.path.join(paths.loading_dir, "tray.clf.joblib")
        if os.path.isfile(tp):
            return joblib.load(tp)

    def _image_path(self, pos):
        loadname = self._loading_manager.load_instance.name
        dirname = os.path.join(paths.loading_dir, loadname)
        p = os.path.join(dirname, "{}.empty.tif".format(pos))
        return p

    def _crop(self, frame, dim=None, pos=1):
        if dim is None:
            hole = self._loading_manager.stage_manager.stage_map.get_hole(pos)
            dim = hole.dimension

        cw = ch = ceil(dim * 2.55)

        pxpermm = self._loading_manager.stage_manager.autocenter_manager.pxpermm
        cw_px = int(cw * pxpermm)
        ch_px = int(ch * pxpermm)
        print("fffffff", dim, pxpermm)
        w, h = get_size(frame)
        x = int((w - cw_px) / 2.0)
        y = int((h - ch_px) / 2.0)
        return asarray(crop(frame, x, y, cw_px, ch_px))

    def _check_position(self, hole):
        pass

    def _classify(self, frame):
        pipe = self._get_classifier()
        if pipe:
            result = pipe.predict(frame.flatten())
            self.debug(f"classify={result}")

    def _advance(self, label):
        self._labels.append(LABEL_MAP.get(label, -1))
        self._samples.append(self._active_frame)

        trayname = self._loading_manager.stage_manager.stage_map.name
        name = f"{trayname}-{self._active_position}"
        self._add_labeled_sample(name, self._active_frame, label)
        self._visit_next_position()

    def _add_unlabeled_image(self, load_pos, pos, frame):
        host = ""
        url = f"http://{host}/unclassified_image"

        buf = io.BytesIO()
        im = Image.fromarray(frame)
        im.save(buf, "tiff")

        trayname = self._loading_manager.stage_manager.stage_map.name

        data = {
            "trayname": trayname,
            "hole_id": pos,
            "image": base64.b64encode(buf.getvalue()).decode(),
        }

        load_pos = self._loading_manager.get_load_position_by_position(pos)
        if load_pos:
            data["identifier"] = load_pos.identifier
            data["sample"] = load_pos.sample
            data["material"] = load_pos.material
            data["project"] = load_pos.project
            data["note"] = load_pos.note
            data["nxtals"] = load_pos.nxtals
            data["weight"] = load_pos.weight

        if self._loading_manager.load_instance:
            data["loadname"] = self._loading_manager.load_instance.name

        resp = requests.post(url, json=data)

    def _add_labeled_sample(self, name, frame, label):
        pass

    def _stop_button_fired(self):
        self.stop()

    def _good_button_fired(self):
        self._advance("good")

    def _empty_button_fired(self):
        self._advance("empty")

    def _multigrain_button_fired(self):
        self._advance("multigrain")

    def _contaminant_button_fired(self):
        self._advance("contaminant")


def main():
    tc = TrayChecker(None, dbpath="/Users/ross/Sandbox/loadimages/db.sqlite")
    # tc.train_ml()
    tc.test_ml()


if __name__ == "__main__":
    paths.build("~/PychronDev")
    logging_setup("traydb")
    main()

# ============= EOF =============================================
