import numpy as np, wave
from scipy.signal import lfilter, butter, fftconvolve
SR=44100; DUR=30.0; N=int(SR*DUR)
rng=np.random.default_rng(11)
L=np.zeros(N); Rr=np.zeros(N); WL=np.zeros(N); WR=np.zeros(N)  # dry / wet(reverb send)
def add(sig,t,g=1.0,pan=0.0,wet=0.0):
    i=int(t*SR)
    if i>=N: return
    j=min(N,i+len(sig)); s=sig[:j-i]*g
    gl=np.cos((pan+1)*np.pi/4); gr=np.sin((pan+1)*np.pi/4)
    L[i:j]+=s*gl; Rr[i:j]+=s*gr
    if wet: WL[i:j]+=s*gl*wet; WR[i:j]+=s*gr*wet
def tt(d): return np.arange(int(d*SR))/SR
def lp(x,fc,o=2): b,a=butter(o,fc/(SR/2),'low'); return lfilter(b,a,x)
def hp(x,fc,o=2): b,a=butter(o,fc/(SR/2),'high'); return lfilter(b,a,x)
def bp(x,lo,hi,o=2): b,a=butter(o,[lo/(SR/2),hi/(SR/2)],'band'); return lfilter(b,a,x)
def env(n,a,d,s=0.0,r=0.0):  # a,d seconds fraction -> exp decay
    t=np.arange(n)/SR; e=np.minimum(1,t/max(a,1e-4))*np.exp(-t/max(d,1e-4)); return e
def hz(m): return 440*2**((m-69)/12)
def saw(f,d,ph=0.0): t=tt(d); return 2*((t*f+ph)%1)-1
def sq(f,d): return np.sign(np.sin(2*np.pi*f*tt(d)))
def sine(f,d): return np.sin(2*np.pi*f*tt(d))
def kick(g=1.0,t0=0.0):
    d=0.42; t=tt(d); f=45+90*np.exp(-t*28); ph=2*np.pi*np.cumsum(f)/SR
    s=np.sin(ph)*np.exp(-t*8.5)+0.35*np.sin(2*ph*0.5)*np.exp(-t*14)
    s+=0.25*lp(rng.standard_normal(len(t)),5000)*np.exp(-t*120)
    return s
def clap():
    d=0.32; n=int(d*SR); x=bp(rng.standard_normal(n),1100,4500); t=np.arange(n)/SR
    e=np.exp(-t*22)+0.6*np.exp(-np.maximum(t-0.012,0)*40)*(t>0.012)+0.5*np.exp(-np.maximum(t-0.026,0)*40)*(t>0.026)
    return x*e*1.2
def hat(op=False):
    d=0.25 if op else 0.07; n=int(d*SR); x=hp(rng.standard_normal(n),7000); return x*np.exp(-np.arange(n)/SR*(18 if op else 70))
def pluck(m,d=0.28):
    f=hz(m); x=saw(f,d)+0.5*saw(f*1.005,d,0.3); n=len(x); t=np.arange(n)/SR
    # sweeping lowpass per pluck via two bands blend
    a=lp(x,1800); b=lp(x,5200); w=np.exp(-t*14)
    return (a*(1-w)+b*w)*np.exp(-t*9)*0.6
def bass(m,d=0.45):
    f=hz(m); t=tt(d); x=np.sin(2*np.pi*f*t)+0.35*lp(saw(f,d),500); return x*np.minimum(1,t/0.006)*np.exp(-t*3.2)
def padnote(m,d,att=0.5):
    x=sum(saw(hz(m)*c,d,p) for c,p in ((1,0),(1.004,.3),(0.996,.6)))/3; x=lp(x,1100,2)
    t=tt(d); e=np.minimum(1,t/att)*np.minimum(1,(d-t)/0.6)
    return x*e
def bell(m,d=2.5):
    f=hz(m); t=tt(d); x=sum(a*np.sin(2*np.pi*f*r*t)*np.exp(-t*dc) for r,a,dc in ((1,1,1.6),(2.76,.5,2.6),(5.4,.3,3.8),(8.93,.18,5)))
    return x*np.minimum(1,t/0.003)
def noise_sweep(d,f0,f1,shape='in',res=0.0):
    n=int(d*SR); x=rng.standard_normal(n); out=np.zeros(n); z=0.0; ch=256
    zi=np.zeros(1)
    f=np.geomspace(f0,f1,n)
    for i in range(0,n,ch):
        fc=f[min(i+ch//2,n-1)]; a=1-np.exp(-2*np.pi*fc/SR); y,zi=lfilter([a],[1,-(1-a)],x[i:i+ch],zi=zi); out[i:i+ch]=y
    out=out-lp(out,max(40,min(f0,f1)/2),1)  # tame lows
    t=np.arange(n)/SR; k=t/d
    e=(k**2.2) if shape=='in' else ((1-k)**1.8 if shape=='out' else np.sin(np.pi*k)**1.4)
    o=out*e; return o/(np.max(np.abs(o))+1e-9)
def whoosh(d=0.6,up=True): return noise_sweep(d,400,6000 if up else 300,'mid')*0.6 if up else noise_sweep(d,6000,300,'mid')*0.6
def tick(f=2200,d=0.05): t=tt(d); return np.sin(2*np.pi*f*t)*np.exp(-t*90)*0.7
def pop(f=520,d=0.14): t=tt(d); fr=f*(1+1.2*np.exp(-t*40)); return np.sin(2*np.pi*np.cumsum(fr)/SR)*np.exp(-t*26)
def boom(d=1.6,g=1.0):
    t=tt(d); f=60*np.exp(-t*1.4)+28; x=np.sin(2*np.pi*np.cumsum(f)/SR)*np.exp(-t*2.2)
    cr=lp(rng.standard_normal(len(t)),9000)*np.exp(-t*3.2)*0.35
    return (x+cr)*g
def impact(d=1.0): t=tt(d); return boom(d,0.9)+0.5*hp(rng.standard_normal(len(t)),3000)*np.exp(-t*9)*0.35
# ---------- музыка ----------
BEAT=0.5; BAR=2.0
CH=[(57,[57,60,64]),(53,[53,57,60]),(48,[48,52,55]),(55,[55,59,62])]  # Am F C G
def chord(bar): return CH[bar%4]
# пэд
for b in range(15):
    root,notes=chord(b); t0=b*BAR
    for m in notes: add(padnote(m+12-12,BAR+0.5),t0,0.07,pan=(m%3-1)*0.5,wet=0.6)
# бас + кик + хэты + клап + арпеджио
kicks=[]
for b in range(15):
    root,notes=chord(b); t0=b*BAR
    for k in range(4):
        tb=t0+k*BEAT
        if tb>=4.0 and tb<26.0:
            kicks.append(tb); add(kick(),tb,0.95)
        if 26.0<=tb<29.0 and k==0: add(kick(),tb,0.6)
        if 2.0<=tb<4.0 and k%2==0: add(kick(),tb,0.4)   # нарастание
    # бас восьмыми с 4.0
    for k in range(8):
        tb=t0+k*BEAT/2
        if 4.0<=tb<26.0: add(bass(root-12),tb,0.5 if k%2==0 else 0.32)
    if 26.0<=t0: add(bass(root-12,1.8),t0,0.5)
    # хэты и клап
    for k in range(8):
        tb=t0+k*BEAT/2
        if 6.0<=tb<26.0 and k%2==1: add(hat(),tb,0.22,pan=0.3)
    for k in (1,3):
        tb=t0+k*BEAT
        if 6.0<=tb<26.0: add(clap(),tb,0.32,pan=-0.1,wet=0.25)
    # арпеджио 16-ми: S2 тихо, S3 мягче, S4/S5 ярче
    for k in range(16):
        tb=t0+k*BEAT/4
        if 4.0<=tb<26.0:
            lvl=0.10 if tb<10 else (0.07 if tb<16 else 0.13)
            pat=[0,1,2,1,0,2,1,2][k%8]
            m=notes[pat]+24+(12 if (k%8)==7 else 0)
            add(pluck(m),tb,lvl,pan=((k%4)-1.5)*0.3,wet=0.35)
# финальный пэд-расцвет
for m in (57,60,64,69): add(padnote(m,4.2,1.0),26.0,0.09,wet=0.7)
# ---------- SFX ----------
# S1
add(impact(0.9),0.40,0.55); add(impact(0.9),0.90,0.5)
for i,m in enumerate([57,60,64,69,72,76,81,84,88,91,93,96,100]): add(tick(hz(m)*1.0,0.07),1.7+i*0.05,0.12,pan=(i%2)*0.6-0.3,wet=0.5)
add(noise_sweep(1.4,300,8000,'in'),2.2,0.55,wet=0.3)
add(sine(220,1.4)*np.linspace(0,1,int(1.4*SR))**2,2.2,0.12)
add(boom(2.0),3.55,1.0,wet=0.3)
# S2
add(whoosh(.5,True),3.7,0.35); add(bell(81),3.85,0.28,wet=0.6); add(bell(88),4.0,0.18,wet=0.6); add(whoosh(.5,True),4.3,0.3)
add(whoosh(.6,False),5.3,0.3)
for i in range(8): add(tick(1800+i*120,0.05),5.7+i*0.07,0.16,pan=-0.3+i*0.08)
for i in range(3): add(whoosh(.7,True),6.2+i*0.25,0.4,pan=(i-1)*0.6); add(pop(300,0.2),7.2+i*0.25,0.28,pan=(i-1)*0.6)
for i in range(4): add(pop(600+i*110),8.2+i*0.18,0.3,pan=-0.4+i*0.27,wet=0.2)
add(whoosh(.5,False),9.55,0.4)
# S3
add(impact(0.8),10.0,0.5); add(whoosh(.7,True),10.0,0.4); add(whoosh(.5,True),10.25,0.3)
for t0,f in ((11.15,520),(12.1,660),(13.25,520)): add(pop(f),t0,0.4,pan=-0.2 if f==520 else 0.2,wet=0.2); add(tick(2600,0.04),t0+0.06,0.15)
for i in range(6): add(tick(900,0.04),12.7+i*0.0833,0.1)
add(bell(88),14.15,0.32,wet=0.6); add(bell(95),14.25,0.2,wet=0.6); add(whoosh(.6,True),14.1,0.3)
add(whoosh(.5,False),15.5,0.35)
# S4
add(impact(0.8),16.0,0.5); add(whoosh(.7,True),16.05,0.4); add(whoosh(.5,True),16.6,0.25)
add(noise_sweep(.9,800,3500,'mid'),17.6,0.14,pan=0.1)
add(pop(900,0.08),18.55,0.4); add(tick(1500,0.03),18.57,0.35); add(boom(0.4,0.35),18.55,0.4)
add(noise_sweep(.6,800,3500,'mid'),19.3,0.12,pan=0.2)
for i,m in enumerate([76,81,84,88]): add(bell(m,1.2),19.8+i*0.1,0.14,pan=-0.3+i*0.2,wet=0.5)
add(whoosh(.5,False),21.5,0.35)
# S5
add(impact(0.8),22.0,0.5); add(whoosh(.6,True),22.0,0.35)
for i in range(5): add(pop(450+i*80),22.0+i*0.12+0.6,0.26,pan=-0.6+i*0.3,wet=0.25)
add(whoosh(.6,True),23.0,0.35); add(tick(1800,0.05),23.8,0.2)
add(noise_sweep(.9,300,9000,'in'),25.0,0.6,wet=0.3)
# S6
add(boom(2.6),25.95,1.0,wet=0.3); add(bell(69,3.5),26.15,0.35,wet=0.7); add(bell(76,3.5),26.2,0.25,wet=0.7); add(bell(81,3.5),26.3,0.2,wet=0.7); add(bell(88,3.5),26.4,0.12,wet=0.7)
add(whoosh(.7,True),26.5,0.35); add(tick(2200,0.05),27.3,0.2); add(pop(700),28.0,0.28,wet=0.2)
# ---------- сведение ----------
ir_n=int(1.6*SR); ir=rng.standard_normal(ir_n)*np.exp(-np.arange(ir_n)/SR*3.0); ir=lp(ir,6500)
def rev(x): return fftconvolve(x,ir)[:N]*0.045
# сайдчейн под кик
duck=np.ones(N)
for tb in kicks:
    i=int(tb*SR); n=int(0.22*SR); duck[i:i+n]=np.minimum(duck[i:i+n],np.linspace(0.55,1,n))
mixL=L*1.0+rev(WL); mixR=Rr*1.0+rev(WR)
out=np.stack([mixL,mixR],1)
# fade-in/out
fi=np.minimum(1,np.arange(N)/(0.03*SR)); fo=np.minimum(1,(N-np.arange(N))/(0.8*SR))
out=out*(fi*fo)[:,None]
peak=np.max(np.abs(out)); print('peak',peak)
out=np.tanh(out/1.6*1.3)*0.65
data=(out*32767).astype(np.int16)
with wave.open('music.wav','wb') as w: w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(data.tobytes())
print('rms',np.sqrt(np.mean(out**2)),'peak',np.max(np.abs(out)))
