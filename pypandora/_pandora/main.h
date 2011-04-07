/*
 * main.h
 *
 *  Created on: Feb 15, 2011
 *      Author: amoffat
 */

#ifndef MAIN_H_
#define MAIN_H_

#define DEF_PANDORA_FN(name) static PyObject* pandora_##name(PyObject *, PyObject *)

DEF_PANDORA_FN(decrypt);
DEF_PANDORA_FN(encrypt);
DEF_PANDORA_FN(playMusic);
DEF_PANDORA_FN(playSound);
DEF_PANDORA_FN(getMusicStats);
DEF_PANDORA_FN(musicIsPlaying);
DEF_PANDORA_FN(pauseMusic);
DEF_PANDORA_FN(setMusicSpeed);
DEF_PANDORA_FN(getVolume);
DEF_PANDORA_FN(setVolume);
DEF_PANDORA_FN(stopMusic);
DEF_PANDORA_FN(unpauseMusic);

#endif /* MAIN_H_ */
