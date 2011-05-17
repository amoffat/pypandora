/*
 * main.h
 *
 *  Created on: Feb 15, 2011
 *      Author: amoffat
 */

#ifndef MAIN_H_
#define MAIN_H_

#define DEF_PANDORA_FN_H(name) static PyObject* pandora_##name(PyObject *, PyObject *)
#define DEF_PANDORA_FN(name) static PyObject* pandora_##name(PyObject *self, PyObject *args)

DEF_PANDORA_FN_H(decrypt);
DEF_PANDORA_FN_H(encrypt);
DEF_PANDORA_FN_H(playMusic);
DEF_PANDORA_FN_H(playSound);
DEF_PANDORA_FN_H(getMusicStats);
DEF_PANDORA_FN_H(musicIsPlaying);
DEF_PANDORA_FN_H(pauseMusic);
DEF_PANDORA_FN_H(setMusicSpeed);
DEF_PANDORA_FN_H(getVolume);
DEF_PANDORA_FN_H(setVolume);
DEF_PANDORA_FN_H(stopMusic);
DEF_PANDORA_FN_H(unpauseMusic);
DEF_PANDORA_FN_H(update);

static float _pandora_setVolume(float new_volume);

#endif /* MAIN_H_ */
