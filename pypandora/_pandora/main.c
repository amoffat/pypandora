#include <python2.6/Python.h>
#include "main.h"
#include "crypt.h"
#include <fmodex/fmod.h>
#include <fmodex/fmod_errors.h>
#include <time.h>


#ifndef MIN
#define MIN(a, b) ((a) > (b) ? (b) : (a))
#endif

#ifndef MAX
#define MAX(a, b) ((a) < (b) ? (b) : (a))
#endif

#define CLAMP(x, lo, hi) MIN((hi), MAX((lo), (x)))



static FMOD_SYSTEM* sound_system = NULL;
static FMOD_SOUND* music = NULL;
static FMOD_SOUND* cue = NULL;
static FMOD_CHANNEL* music_channel = 0;
static FMOD_CHANNEL* cue_channel = 0;
static float original_frequency = 0;
static float volume = 0.5f;


static PyMethodDef pandora_methods[] = {
    {"decrypt",  pandora_decrypt, METH_VARARGS, "Decrypt a string from pandora"},
    {"encrypt",  pandora_encrypt, METH_VARARGS, "Encrypt a string from pandora"},
    {"play",  pandora_playMusic, METH_VARARGS, "Play a song"},
    {"play_cue",  pandora_playCue, METH_VARARGS, "Play an arbitrary sound effect"},
    {"pause",  pandora_pauseMusic, METH_VARARGS, "Pause music"},
    {"resume",  pandora_unpauseMusic, METH_VARARGS, "Resume music"},
    {"stop",  pandora_stopMusic, METH_VARARGS, "Stop music"},
    {"is_playing",  pandora_musicIsPlaying, METH_VARARGS, "See if anything is playing"},
    {"set_speed", pandora_setMusicSpeed, METH_VARARGS, "Set the music speed"},
    {"set_volume", pandora_setVolume, METH_VARARGS, "Set the volume"},
    {"get_volume", pandora_getVolume, METH_VARARGS, "Get the volume"},
    {"stats",  pandora_getMusicStats, METH_VARARGS, "Get music stats"},
    {NULL, NULL, 0, NULL}
};


void pandora_fmod_errcheck(FMOD_RESULT result) {
    if (result != FMOD_OK) {
        printf("FMOD error! (%d) %s\n", result, FMOD_ErrorString(result));
        exit(-1);
    }
}



static PyObject* pandora_decrypt(PyObject *self, PyObject *args) {
    const char *payload;
    char *xml;

    if (!PyArg_ParseTuple(args, "s", &payload)) return NULL;

    xml = PianoDecryptString(payload);
    return Py_BuildValue("s", xml);
}


static PyObject* pandora_getMusicStats(PyObject *self, PyObject *args) {
    if (!music_channel) Py_RETURN_NONE;

    unsigned int pos;
    (void)FMOD_Channel_GetPosition(music_channel, &pos, FMOD_TIMEUNIT_MS);

    unsigned int length;
    (void)FMOD_Sound_GetLength(music, &length, FMOD_TIMEUNIT_MS);
    return Py_BuildValue("(ii)", (int)((float)length / 1000.0f), (int)((float)pos / 1000.0f));
}


static PyObject* pandora_setVolume(PyObject *self, PyObject *args) {
    if (!PyArg_ParseTuple(args, "f", &volume)) return NULL;

    volume = CLAMP(volume, 0.0, 1.0);

    if (!music_channel) return Py_BuildValue("f", volume);

    FMOD_RESULT res;
    res = FMOD_Channel_SetVolume(music_channel, volume);
    pandora_fmod_errcheck(res);
    if (cue_channel) {
        res = FMOD_Channel_SetVolume(cue_channel, volume);
        pandora_fmod_errcheck(res);
    }

    return Py_BuildValue("f", volume);
}


static PyObject* pandora_getVolume(PyObject *self, PyObject *args) {
    if (!music_channel) return Py_BuildValue("f", volume);

    FMOD_RESULT res;

    res = FMOD_Channel_GetVolume(music_channel, &volume);
    pandora_fmod_errcheck(res);

    return Py_BuildValue("f", volume);
}


static PyObject* pandora_musicIsPlaying(PyObject *self, PyObject *args) {
    FMOD_BOOL isplaying = 0;
    FMOD_RESULT res;

    if (music_channel) {
        res = FMOD_Channel_IsPlaying(music_channel, &isplaying);
        pandora_fmod_errcheck(res);
    }

    if (isplaying) {Py_RETURN_TRUE;}
    else {Py_RETURN_FALSE;}
}


static PyObject* pandora_setMusicSpeed(PyObject *self, PyObject *args) {
    FMOD_RESULT res;

    float new_freq;
    if (!PyArg_ParseTuple(args, "f", &new_freq)) return NULL;

    if (music_channel) {
        res = FMOD_Channel_SetFrequency(music_channel, new_freq * original_frequency);
        pandora_fmod_errcheck(res);
    }
    Py_RETURN_NONE;
}


static PyObject* pandora_stopMusic(PyObject *self, PyObject *args) {
    FMOD_RESULT res;

    if (music_channel) {
        res = FMOD_Channel_Stop(music_channel);
        music_channel = 0;
        pandora_fmod_errcheck(res);
    }
    if (music) {
        res = FMOD_Sound_Release(music);
        music = NULL;
        pandora_fmod_errcheck(res);
    }

    Py_RETURN_NONE;
}


static PyObject* pandora_pauseMusic(PyObject *self, PyObject *args) {
    FMOD_RESULT res;
    FMOD_BOOL isplaying;

    if (music_channel) {
        res = FMOD_Channel_IsPlaying(music_channel, &isplaying);
        pandora_fmod_errcheck(res);
        if (isplaying) {
            res = FMOD_Channel_SetPaused(music_channel, 1);
            pandora_fmod_errcheck(res);
        }
    }
    Py_RETURN_NONE;
}


static PyObject* pandora_unpauseMusic(PyObject *self, PyObject *args) {
    FMOD_RESULT res;
    FMOD_BOOL isplaying;

    if (music_channel) {
        res = FMOD_Channel_IsPlaying(music_channel, &isplaying);
        pandora_fmod_errcheck(res);
        if (isplaying) {
            res = FMOD_Channel_SetPaused(music_channel, 0);
            pandora_fmod_errcheck(res);
        }
    }
    Py_RETURN_NONE;
}

static PyObject* pandora_playCue(PyObject *self, PyObject *args) {
    const char *cue_file;
    FMOD_RESULT res;

    if (!PyArg_ParseTuple(args, "s", &cue_file)) return NULL;

    res = FMOD_System_CreateSound(sound_system, cue_file, FMOD_SOFTWARE, 0, &cue);
    pandora_fmod_errcheck(res);
    res = FMOD_System_PlaySound(sound_system, FMOD_CHANNEL_FREE, cue, 0, &cue_channel);
    pandora_fmod_errcheck(res);
    res = FMOD_Channel_SetVolume(cue_channel, 1.0);
    pandora_fmod_errcheck(res);

    Py_RETURN_NONE;
}


static PyObject* pandora_playMusic(PyObject *self, PyObject *args) {
    const char *song_file;

    if (!PyArg_ParseTuple(args, "s", &song_file)) return NULL;

    FMOD_RESULT res;
    if (music != NULL) {
        res = FMOD_Channel_Stop(music_channel);
        pandora_fmod_errcheck(res);

        res = FMOD_Sound_Release(music); // avoid memory leak...
        pandora_fmod_errcheck(res);
    }
    res = FMOD_System_CreateSound(sound_system, song_file, FMOD_SOFTWARE, 0, &music);
    PyMem_Free(song_file); // avoid memory leaks
    pandora_fmod_errcheck(res);
    res = FMOD_System_PlaySound(sound_system, FMOD_CHANNEL_REUSE, music, 0, &music_channel);
    pandora_fmod_errcheck(res);

    res = FMOD_Channel_GetFrequency(music_channel, &original_frequency);
    pandora_fmod_errcheck(res);

    res = FMOD_Channel_SetVolume(music_channel, volume);
    pandora_fmod_errcheck(res);

    unsigned int length;
    res = FMOD_Sound_GetLength(music, &length, FMOD_TIMEUNIT_MS);
    pandora_fmod_errcheck(res);
    return Py_BuildValue("i", (int)((float)length / 1000.0f));
}


static PyObject* pandora_encrypt(PyObject *self, PyObject *args) {
    const char *xml;
    char *payload;

    if (!PyArg_ParseTuple(args, "s", &xml)) return NULL;

    payload = PianoEncryptString(xml);
    PyMem_Free(xml); // avoid memory leaks
    return Py_BuildValue("s", payload);
}


static void pandora_cleanup(void) {
    if (music) (void)FMOD_Sound_Release(music);
    if (cue) (void)FMOD_Sound_Release(cue);

    (void)FMOD_System_Close(sound_system);
    (void)FMOD_System_Release(sound_system);
}


PyMODINIT_FUNC init_pandora(void) {
    FMOD_System_Create(&sound_system);
    FMOD_System_Init(sound_system, 32, FMOD_INIT_NORMAL, NULL);

    (void) Py_InitModule("_pandora", pandora_methods);

    Py_AtExit(pandora_cleanup);
}
