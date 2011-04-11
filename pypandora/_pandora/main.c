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

#define GAIN_TARGET 89.0f

static FMOD_SYSTEM* sound_system = NULL;
static FMOD_SOUND* music = NULL;
static FMOD_CHANNEL* music_channel = 0;
static FMOD_CHANNEL* fx_channel = 0;
static float original_frequency = 0;
static float volume = 0.5f;
static float current_gain = 0.0f;



typedef struct {
    const char* filename;
    FMOD_SOUND* sound;
} pandora_soundEffect;


static pandora_soundEffect** sound_effects = NULL;
static int num_effects = 0;


static PyMethodDef pandora_methods[] = {
    {"decrypt",  pandora_decrypt, METH_VARARGS, "Decrypt a string from pandora"},
    {"encrypt",  pandora_encrypt, METH_VARARGS, "Encrypt a string from pandora"},
    {"play",  pandora_playMusic, METH_VARARGS, "Play a song"},
    {"play_sound",  pandora_playSound, METH_VARARGS, "Play an arbitrary sound effect"},
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


static float _pandora_setVolume(float new_volume) {
    new_volume = CLAMP(new_volume, 0.0, 1.0);
    volume = new_volume;

    // adjust for the gain
    new_volume = new_volume * ((GAIN_TARGET + current_gain) / GAIN_TARGET);
    new_volume = CLAMP(new_volume, 0.0, 1.0);

    FMOD_RESULT res;

    if (music_channel) {
        res = FMOD_Channel_SetVolume(music_channel, new_volume);
        pandora_fmod_errcheck(res);
    }

    return new_volume;
}

static PyObject* pandora_setVolume(PyObject *self, PyObject *args) {
    float new_volume;

    if (!PyArg_ParseTuple(args, "f", &new_volume)) return NULL;
    new_volume =_pandora_setVolume(new_volume);
    return Py_BuildValue("f", new_volume);
}


static PyObject* pandora_getVolume(PyObject *self, PyObject *args) {
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

static PyObject* pandora_playSound(PyObject *self, PyObject *args) {
    const char *filename;
    FMOD_RESULT res;

    if (!PyArg_ParseTuple(args, "s", &filename)) return NULL;

    pandora_soundEffect* effect;
    int found = 0, i = 0;
    for (i=0; i<num_effects; ++i) {
        effect = sound_effects[i];
        if (strcmp(effect->filename, filename) == 0) {
            found = 1; break;
        }
    }

    if (!found) {
        effect = (pandora_soundEffect*)malloc(sizeof(pandora_soundEffect));
        effect->filename = strdup(filename);

        sound_effects = (pandora_soundEffect**)realloc(sound_effects, sizeof(pandora_soundEffect *) * ++num_effects);
        if (NULL == sound_effects) return NULL;

        res = FMOD_System_CreateSound(sound_system, effect->filename, FMOD_SOFTWARE, 0, &effect->sound);
        pandora_fmod_errcheck(res);

        sound_effects[num_effects - 1] = effect;
    }

    res = FMOD_System_PlaySound(sound_system, FMOD_CHANNEL_FREE, effect->sound, 0, &fx_channel);
    pandora_fmod_errcheck(res);
    res = FMOD_Channel_SetVolume(fx_channel, CLAMP(4 * volume, 0.0, 1.0));
    pandora_fmod_errcheck(res);
    res = FMOD_Channel_SetPriority(fx_channel, 10); // not as high priority as music
    pandora_fmod_errcheck(res);

    Py_RETURN_NONE;
}


static PyObject* pandora_playMusic(PyObject *self, PyObject *args) {
    const char *song_file;

    if (!PyArg_ParseTuple(args, "sf", &song_file, &current_gain)) return NULL;

    FMOD_RESULT res;
    if (music != NULL) {
        res = FMOD_Channel_Stop(music_channel);
        pandora_fmod_errcheck(res);

        res = FMOD_Sound_Release(music); // avoid memory leak...
        pandora_fmod_errcheck(res);
    }
    res = FMOD_System_CreateSound(sound_system, song_file, FMOD_SOFTWARE, 0, &music);
    pandora_fmod_errcheck(res);
    res = FMOD_System_PlaySound(sound_system, FMOD_CHANNEL_FREE, music, 0, &music_channel);
    pandora_fmod_errcheck(res);
    res = FMOD_Channel_SetPriority(music_channel, 0); // high priority
    pandora_fmod_errcheck(res);

    res = FMOD_Channel_GetFrequency(music_channel, &original_frequency);
    pandora_fmod_errcheck(res);

    _pandora_setVolume(volume);

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
    return Py_BuildValue("s", payload);
}


static void pandora_cleanup(void) {
    if (music) (void)FMOD_Sound_Release(music);

    int i;
    for (i=0; i<num_effects; ++i) {
        (void)FMOD_Sound_Release(sound_effects[i]->sound);
        free((char *)sound_effects[i]->filename);
    }
    if (sound_effects) free(sound_effects);

    (void)FMOD_System_Close(sound_system);
    (void)FMOD_System_Release(sound_system);
}


PyMODINIT_FUNC init_pandora(void) {
    FMOD_System_Create(&sound_system);
    FMOD_System_Init(sound_system, 32, FMOD_INIT_NORMAL, NULL);

    (void) Py_InitModule("_pandora", pandora_methods);

    Py_AtExit(pandora_cleanup);
}
