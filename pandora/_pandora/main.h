/*
 * main.h
 *
 *  Created on: Feb 15, 2011
 *      Author: amoffat
 */

#ifndef MAIN_H_
#define MAIN_H_

static PyObject* pandora_decrypt(PyObject *, PyObject *);
static PyObject* pandora_encrypt(PyObject *, PyObject *);
static PyObject* pandora_playMusic(PyObject *, PyObject *);
static PyObject* pandora_getMusicStats(PyObject *, PyObject *);
static PyObject* pandora_musicIsPlaying(PyObject *, PyObject *);
static PyObject* pandora_pauseMusic(PyObject *, PyObject *);
static PyObject* pandora_setMusicSpeed(PyObject *, PyObject *);
static PyObject* pandora_setVolume(PyObject *, PyObject *);
static PyObject* pandora_stopMusic(PyObject *, PyObject *);
static PyObject* pandora_unpauseMusic(PyObject *, PyObject *);

#endif /* MAIN_H_ */
