import wave,sys,subprocess,numpy as np
from piper import PiperVoice
try:
    from piper import SynthesisConfig
except Exception: SynthesisConfig=None
name=sys.argv[1]; LS=float(sys.argv[2]) if len(sys.argv)>2 else 1.0
v=PiperVoice.load(f'ru_RU-{name}-medium.onnx')
print('sr',v.config.sample_rate)
# (старт с, текст, макс. длительность с)
LINES=[(0.55,"Не с кем идти?",1.3),(1.95,"Кому-то тоже.",1.5),
 (4.45,"Попл. Учёба и общение Алма-Ю в одном месте.",3.2),(7.4,"Расписание, оценки, задания и экзамены.",2.9),
 (10.8,"Анонимный чат: ник вместо имени, пока сам не раскроешься.",4.0),
 (16.8,"Отметься «Иду» и найди, с кем пойти.",2.6),(19.65,"Анонимно соединим с тем, кто тоже идёт.",2.75),
 (22.45,"Клубы Алма-Ю уже здесь. Запись на походы Саммит Сикерс.",3.7),
 (26.7,"Попл. Зайди и попробуй.",2.3)]
SR=v.config.sample_rate; out=np.zeros(int(30.5*44100))
for i,(t0,txt,mx) in enumerate(LINES):
    fn=f'{name}_{i}.wav'
    with wave.open(fn,'wb') as w:
        if SynthesisConfig: v.synthesize_wav(txt,w,syn_config=SynthesisConfig(length_scale=LS*(0.85 if i==3 else 1.0)))
        else: v.synthesize(txt,w)
    d=wave.open(fn).getnframes()/SR
    tempo=max(1.0,d/mx)
    subprocess.run(['ffmpeg','-y','-loglevel','error','-i',fn,'-ar','44100','-ac','1','-filter:a',f'atempo={min(tempo,1.5):.3f}',f'{name}_{i}_f.wav'],check=True)
    x=np.frombuffer(wave.open(f'{name}_{i}_f.wav').readframes(10**9),dtype=np.int16).astype(float)/32768
    print(i,round(d,2),'tempo',round(tempo,2),'->',round(len(x)/44100,2))
    j=int(t0*44100); out[j:j+len(x)]+=x[:len(out)-j]
out=out/np.max(np.abs(out))*0.9
with wave.open(f'voice_{name}.wav','wb') as w:
    w.setnchannels(1);w.setsampwidth(2);w.setframerate(44100);w.writeframes((out*32767).astype(np.int16).tobytes())
