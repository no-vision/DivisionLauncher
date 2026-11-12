/* Private D3D11 overlay. Public COM interfaces only, no game RVAs.
 * Deferred command list + ExecuteCommandList(TRUE) restores the game's complete
 * pipeline. No retained backbuffer or per-Present JS/network/filesystem calls.
 * All mutable storage is imported because CModule globals are read-only.
 */
#ifndef OVERLAY_TEST
#include <gum/guminterceptor.h>
#endif
#include <stdint.h>
#include <string.h>
#define MAX_WIDTH 1200
#define MAX_HEIGHT 900
#define PIXELS (MAX_WIDTH * MAX_HEIGHT * 4)
#define PANELS 3
#define INPUTS 64
#define METHOD(Object, Slot, Type) ((Type)(*(void ***)(Object))[Slot])
typedef struct { uint32_t A; uint16_t B, C; uint8_t D[8]; } Guid;
typedef struct { uint32_t Width,Height,Mips,ArraySize,Format,Samples,Quality,Usage,Bind,Cpu,Misc; } TextureDescription;
typedef struct { float X,Y,Width,Height,Minimum,Maximum; } Viewport;
typedef struct { int32_t X,Y; } Point;
typedef struct { int32_t Left,Top,Right,Bottom; } Rectangle;
typedef struct { uint32_t Size,Flags; void *Cursor; Point Position; } CursorDescription;
typedef struct { int32_t Kind,X,Y,Value,Flags; } InputEvent;
typedef struct { void *Object; uint32_t Kind,Size; } InputDevice;
typedef struct {
    void *Texture,*View;
    uint32_t X,Y,Width,Height,TextureWidth,TextureHeight,Dirty;
    uint8_t Pixels[PIXELS];
} PanelState;
typedef struct {
    void *Device,*Context,*Deferred,*Vertex,*Pixel,*Blend,*Raster;
    uint32_t Enabled,Frames,Copies,Skipped,Errors,LastWidth,LastHeight,LastFormat,LastSamples;
    uint32_t VertexLength,PixelLength;
    uint8_t VertexBytes[8192],PixelBytes[8192];
    uint64_t LastPublish;
    PanelState Panels[PANELS];
    void *Window,*PreviousProcedure;
    int32_t EditMode,Dragging,MouseX,MouseY,InputHead,InputCount;
    InputEvent Events[INPUTS];
    InputDevice Devices[8];
    uint8_t Keys[256],MouseDown;
    uint32_t DirectMouse,DirectKeyboard;
} OverlayState;
extern OverlayState State;
extern uint8_t RenderLock;
extern int TryLock(void *Lock);
extern void Unlock(void *Lock);
extern uint64_t ClockMilliseconds(void);
extern int16_t KeyState(int Key);
extern void *ForegroundWindow(void);
extern intptr_t SetWindowProcedure(void *Window,int Index,intptr_t Procedure);
extern intptr_t CallWindowProcedure(void *Procedure,void *Window,uint32_t Message,uintptr_t WParam,intptr_t LParam);
extern intptr_t DefaultWindowProcedure(void *Window,uint32_t Message,uintptr_t WParam,intptr_t LParam);
extern uint32_t ReadRawInput(void *Handle,uint32_t Command,void *Data,uint32_t *Size,uint32_t HeaderSize);
extern int CursorPosition(Point *Position);
extern int ClientPosition(void *Window,Point *Position);
extern int ClientBounds(void *Window,Rectangle *Bounds);
extern int CursorInformation(CursorDescription *Description);

static const Guid DeviceId={0xdb6f6ddb,0xac77,0x4e88,{0x82,0x53,0x81,0x9d,0xf9,0xbb,0xf1,0x40}};
static const Guid TextureId={0x6f15aaf2,0xd208,0x4e89,{0x9a,0xb4,0x48,0x95,0x35,0xd3,0x4f,0x9c}};
typedef uint32_t (*ReleaseFn)(void *);
typedef int32_t (*GetDeviceFn)(void *,const Guid *,void **);
typedef int32_t (*GetBufferFn)(void *,uint32_t,const Guid *,void **);
typedef void (*DescriptionFn)(void *,TextureDescription *);
typedef void (*ContextFn)(void *,void **);
typedef int32_t (*DeferredFn)(void *,uint32_t,void **);
typedef int32_t (*TextureFn)(void *,const TextureDescription *,const void *,void **);
typedef int32_t (*ViewFn)(void *,void *,const void *,void **);
typedef int32_t (*ShaderFn)(void *,const void *,uintptr_t,void *,void **);
typedef int32_t (*StateFn)(void *,const void *,void **);
typedef void (*UpdateFn)(void *,void *,uint32_t,const void *,const void *,uint32_t,uint32_t);
typedef void (*BindShaderFn)(void *,void *,void **,uint32_t);
typedef void (*BindArrayFn)(void *,uint32_t,uint32_t,void **);
typedef void (*TargetsFn)(void *,uint32_t,void **,void *);
typedef void (*BlendFn)(void *,void *,const float *,uint32_t);
typedef void (*ObjectFn)(void *,void *);
typedef void (*TopologyFn)(void *,uint32_t);
typedef void (*ViewportFn)(void *,uint32_t,const Viewport *);
typedef void (*DrawFn)(void *,uint32_t,uint32_t);
typedef int32_t (*FinishFn)(void *,int,void **);
typedef void (*ExecuteFn)(void *,void *,int);
static void Release(void *Object) { if(Object) METHOD(Object,2,ReleaseFn)(Object); }
static void Clear(void)
{
    uint32_t I;
    for(I=0;I<PANELS;I++) {
        PanelState *Panel=&State.Panels[I]; Release(Panel->View); Release(Panel->Texture);
        Panel->View=Panel->Texture=0; Panel->TextureWidth=Panel->TextureHeight=0; Panel->Dirty=1;
    }
    Release(State.Raster); Release(State.Blend); Release(State.Pixel); Release(State.Vertex);
    Release(State.Deferred); Release(State.Context); Release(State.Device);
    State.Raster=State.Blend=State.Pixel=State.Vertex=State.Deferred=State.Context=State.Device=0;
}
uint32_t StateSize(void) { return sizeof(OverlayState); }
int Shaders(const void *Vertex,uint32_t VertexLength,const void *Pixel,uint32_t PixelLength)
{
    if(!VertexLength || VertexLength>8192 || !PixelLength || PixelLength>8192 || !TryLock(&RenderLock)) return 0;
    memcpy(State.VertexBytes,Vertex,VertexLength); memcpy(State.PixelBytes,Pixel,PixelLength);
    State.VertexLength=VertexLength; State.PixelLength=PixelLength; Unlock(&RenderLock); return 1;
}
int Publish(uint32_t Index,uint32_t X,uint32_t Y,uint32_t Width,uint32_t Height,const uint8_t *Pixels,uint32_t Length)
{
    PanelState *Panel;
    if(Index>=PANELS || Width>MAX_WIDTH || Height>MAX_HEIGHT || X>16384 || Y>16384 || Length!=Width*Height*4 ||
       (Width==0)!=(Height==0) || !TryLock(&RenderLock)) return 0;
    Panel=&State.Panels[Index]; Panel->X=X; Panel->Y=Y; Panel->Width=Width; Panel->Height=Height;
    if(Length) memcpy(Panel->Pixels,Pixels,Length);
    Panel->Dirty=1; State.Enabled=1; State.LastPublish=ClockMilliseconds(); Unlock(&RenderLock); return 1;
}
void Heartbeat(void) { if(TryLock(&RenderLock)) { State.LastPublish=ClockMilliseconds(); Unlock(&RenderLock); } }
void Stop(void)
{
    if(!TryLock(&RenderLock)) return;
    State.Enabled=0;
    if(State.Window && State.PreviousProcedure) SetWindowProcedure(State.Window,-4,(intptr_t)State.PreviousProcedure);
    State.Window=State.PreviousProcedure=0; Clear(); Unlock(&RenderLock);
}
int Snapshot(uint32_t *Output)
{
    if(!TryLock(&RenderLock)) return 0;
    Output[0]=State.Frames; Output[1]=State.Copies; Output[2]=State.Skipped; Output[3]=State.Errors;
    Output[4]=State.LastWidth; Output[5]=State.LastHeight; Output[6]=State.LastFormat; Output[7]=State.LastSamples;
    Unlock(&RenderLock); return 1;
}
static int Create(void *Device)
{
    uint32_t Blend[66],Raster[10];
    State.Device=Device; /* Takes the caller's GetDevice reference. */
    METHOD(Device,40,ContextFn)(Device,&State.Context);
    if(!State.Context || METHOD(Device,27,DeferredFn)(Device,0,&State.Deferred)<0 || !State.Deferred) return 0;
    if(METHOD(Device,12,ShaderFn)(Device,State.VertexBytes,State.VertexLength,0,&State.Vertex)<0 ||
       METHOD(Device,15,ShaderFn)(Device,State.PixelBytes,State.PixelLength,0,&State.Pixel)<0) return 0;
    memset(Blend,0,sizeof(Blend));
    Blend[2]=1; Blend[3]=5; Blend[4]=6; Blend[5]=1; Blend[6]=2; Blend[7]=6; Blend[8]=1; Blend[9]=15;
    /* ONE=2, SRC_ALPHA=5, INV_SRC_ALPHA=6, ADD=1; RGBA write mask15. */
    memset(Raster,0,sizeof(Raster)); Raster[0]=3; Raster[1]=1; Raster[6]=1;
    return METHOD(Device,20,StateFn)(Device,Blend,&State.Blend)>=0 && State.Blend &&
        METHOD(Device,22,StateFn)(Device,Raster,&State.Raster)>=0 && State.Raster;
}
void Draw(void *Chain,uint32_t Flags,int PartialPresent)
{
    void *Buffer=0,*Device=0,*Target=0,*Commands=0;
    TextureDescription Desc,Texture; Viewport View; uint32_t I;
    if(!TryLock(&RenderLock)) return;
    State.Frames++;
    if(!State.Enabled || !State.VertexLength || ClockMilliseconds()-State.LastPublish>4000 || Flags&1 || PartialPresent) goto Skip;
    if(METHOD(Chain,9,GetBufferFn)(Chain,0,&TextureId,&Buffer)<0 || !Buffer) goto Error;
    METHOD(Buffer,10,DescriptionFn)(Buffer,&Desc);
    State.LastWidth=Desc.Width; State.LastHeight=Desc.Height; State.LastFormat=Desc.Format; State.LastSamples=Desc.Samples;
    if(Desc.Width<640 || Desc.Height<360 || Desc.Samples!=1 || Desc.ArraySize!=1 ||
       (Desc.Format!=28 && Desc.Format!=29 && Desc.Format!=87 && Desc.Format!=91)) goto Skip;
    if(METHOD(Chain,7,GetDeviceFn)(Chain,&DeviceId,&Device)<0 || !Device) goto Skip;
    if(Device!=State.Device) {
        Clear(); if(!Create(Device)) { Device=0; Clear(); goto Error; } Device=0;
    }
    if(METHOD(State.Device,9,ViewFn)(State.Device,Buffer,0,&Target)<0 || !Target) goto Error;
    for(I=0;I<PANELS;I++) {
        PanelState *Panel=&State.Panels[I];
        if(!Panel->Width || !Panel->Height) continue;
        if(Panel->X>=Desc.Width || Panel->Y>=Desc.Height) continue;
        if(Panel->TextureWidth!=Panel->Width || Panel->TextureHeight!=Panel->Height) {
            Release(Panel->View); Release(Panel->Texture); Panel->View=Panel->Texture=0;
            Panel->TextureWidth=Panel->TextureHeight=0;
            memset(&Texture,0,sizeof(Texture)); Texture.Width=Panel->Width; Texture.Height=Panel->Height;
            Texture.Mips=Texture.ArraySize=Texture.Samples=1; Texture.Format=28; Texture.Bind=8;
            if(METHOD(State.Device,5,TextureFn)(State.Device,&Texture,0,&Panel->Texture)<0 || !Panel->Texture ||
               METHOD(State.Device,7,ViewFn)(State.Device,Panel->Texture,0,&Panel->View)<0 || !Panel->View) goto Error;
            Panel->TextureWidth=Panel->Width; Panel->TextureHeight=Panel->Height; Panel->Dirty=1;
        }
        if(Panel->Dirty) {
            METHOD(State.Context,48,UpdateFn)(State.Context,Panel->Texture,0,0,Panel->Pixels,Panel->Width*4,0);
            Panel->Dirty=0;
        }
    }
    METHOD(State.Deferred,33,TargetsFn)(State.Deferred,1,&Target,0);
    METHOD(State.Deferred,35,BlendFn)(State.Deferred,State.Blend,0,0xffffffff);
    METHOD(State.Deferred,43,ObjectFn)(State.Deferred,State.Raster);
    METHOD(State.Deferred,11,BindShaderFn)(State.Deferred,State.Vertex,0,0);
    METHOD(State.Deferred,9,BindShaderFn)(State.Deferred,State.Pixel,0,0);
    METHOD(State.Deferred,24,TopologyFn)(State.Deferred,4);
    for(I=0;I<PANELS;I++) {
        PanelState *Panel=&State.Panels[I];
        if(!Panel->Width || !Panel->Height || Panel->X>=Desc.Width || Panel->Y>=Desc.Height) continue;
        View.X=(float)Panel->X; View.Y=(float)Panel->Y;
        View.Width=(float)Panel->Width; View.Height=(float)Panel->Height; View.Minimum=0; View.Maximum=1;
        METHOD(State.Deferred,44,ViewportFn)(State.Deferred,1,&View);
        METHOD(State.Deferred,8,BindArrayFn)(State.Deferred,0,1,&Panel->View);
        METHOD(State.Deferred,13,DrawFn)(State.Deferred,3,0);
    }
    /* FALSE resets the deferred context and releases its backbuffer bindings.
     * TRUE on Execute saves/restores the game's immediate context state. */
    if(METHOD(State.Deferred,114,FinishFn)(State.Deferred,0,&Commands)<0 || !Commands) { Clear(); goto Error; }
    METHOD(State.Context,58,ExecuteFn)(State.Context,Commands,1);
    State.Copies++; goto Done;
Skip: State.Skipped++; goto Done;
Error: State.Errors++; State.Enabled=0;
Done: Release(Commands); Release(Target); Release(Device); Release(Buffer); Unlock(&RenderLock);
}

/* Window input ring. No allocation, JS callbacks or blocking game-thread locks.
 * F8 owns overlay interaction; Shift+mouse provides transient drag/click controls.
 * Visible native cursors use screen-to-client coordinates scaled to the render
 * target. Hidden/relative mouse mode alone uses a software cursor. Unhandled input is forwarded to the original window procedure.
 */
static int Clamp(int Value,int Maximum) { return Value<0?0:Value>Maximum?Maximum:Value; }
/* Do not integrate raw deltas beside a visible desktop cursor: acceleration,
 * monitor origins and render scaling make that second pointer drift. Neither
 * cursor visibility counters nor desktop clipping are changed by this addon. */
static int ReadCursor(int VisibleOnly) {
    CursorDescription Info; Rectangle Bounds; Point Position;
    memset(&Info,0,sizeof(Info)); Info.Size=sizeof(Info);
    if(VisibleOnly) {
        if(!CursorInformation(&Info) || !(Info.Flags&1)) return 0;
        Position=Info.Position;
    } else if(!CursorPosition(&Position)) return 0;
    if(!State.Window || !ClientPosition(State.Window,&Position) ||
       !ClientBounds(State.Window,&Bounds) || Bounds.Right<=0 || Bounds.Bottom<=0 ||
       !State.LastWidth || !State.LastHeight) return 0;
    /* Keep points outside the window outside: clamping them to an edge would
     * incorrectly select an edge panel while the real cursor is on monitor2. */
    State.MouseX=(int32_t)((int64_t)Position.X*State.LastWidth/Bounds.Right);
    State.MouseY=(int32_t)((int64_t)Position.Y*State.LastHeight/Bounds.Bottom);
    return 1;
}
static void BeginEditing(void) {
    if(!ReadCursor(0)) {
        State.MouseX=Clamp(State.MouseX,(int)State.LastWidth-1);
        State.MouseY=Clamp(State.MouseY,(int)State.LastHeight-1);
    }
}
static int Modifiers(void) { return ((KeyState(0x10)&0x8000)?1:0)|((KeyState(0x11)&0x8000)?2:0)|((KeyState(0x12)&0x8000)?4:0); }
static int Inside(int X,int Y) {
    int I; for(I=0;I<2;I++) {
        PanelState *Panel=&State.Panels[I];
        if(Panel->Width && X>=(int)Panel->X && Y>=(int)Panel->Y && X<(int)(Panel->X+Panel->Width) && Y<(int)(Panel->Y+Panel->Height)) return 1;
    } return 0;
}
static void Queue(int Kind,int Value,int Flags) {
    InputEvent *Event;
    if(State.InputCount==INPUTS) { State.InputHead=(State.InputHead+1)%INPUTS; State.InputCount--; }
    Event=&State.Events[(State.InputHead+State.InputCount)%INPUTS];
    Event->Kind=Kind; Event->X=State.MouseX; Event->Y=State.MouseY; Event->Value=Value; Event->Flags=Flags; State.InputCount++;
}
static int InputActive(void) {
    return State.Window && ForegroundWindow()==State.Window && State.Enabled && ClockMilliseconds()-State.LastPublish<4000;
}
/* The render lock may be busy uploading a panel. Input already claimed by
 * editing must never fall through to game actions during that interval. These
 * aligned scalar intent reads are conservative; queue work remains locked. */
static int InputClaimed(void) {
    return InputActive() && (State.EditMode || State.Dragging ||
        ((Modifiers()&1) && Inside(State.MouseX,State.MouseY)));
}
static InputDevice *FindInput(void *Object) {
    int I; for(I=0;I<8;I++) if(State.Devices[I].Object==Object) return &State.Devices[I];
    return 0;
}
int RegisterInput(void *Object,uint32_t Kind) {
    int I,Result=0;
    if(!Object || (Kind!=1 && Kind!=2) || !TryLock(&RenderLock)) return 0;
    for(I=0;I<8;I++) if(!State.Devices[I].Object || State.Devices[I].Object==Object) {
        State.Devices[I].Object=Object; State.Devices[I].Kind=Kind; State.Devices[I].Size=0; Result=1; break;
    }
    Unlock(&RenderLock); return Result;
}
/* DIDATAFORMAT and DIOBJECTDATAFORMAT x64 layouts. Accept only standard
 * relative mouse offsets and keyboard scan-code bytes; other formats pass through. */
void InputFormat(void *Object,const uint8_t *Format) {
    InputDevice *Device; uint32_t I,Count,Stride,Size,Flags; const uint8_t *Objects; int Valid=1;
    if(!Format || !TryLock(&RenderLock)) return;
    Device=FindInput(Object); if(!Device) goto Done;
    Device->Size=0;
    if(*(const uint32_t *)Format!=32) goto Done;
    Stride=*(const uint32_t *)(Format+4); Flags=*(const uint32_t *)(Format+8);
    Size=*(const uint32_t *)(Format+12); Count=*(const uint32_t *)(Format+16);
    Objects=*(const uint8_t *const *)(Format+24);
    if(Stride!=24 || !Objects || Count>256 || !(Flags&2)) goto Done;
    if(Device->Kind==1) {
        if((Size!=16 && Size!=20) || Count!=Size-9) goto Done;
        for(I=0;I<Count;I++) if(*(const uint32_t *)(Objects+I*Stride+8)!=(I<3?I*4:I+9)) Valid=0;
    } else {
        if(Size!=256 || Count!=256) goto Done;
        for(I=0;I<Count;I++) if(*(const uint32_t *)(Objects+I*Stride+8)!=I) Valid=0;
    }
    if(Valid) Device->Size=Size;
Done: Unlock(&RenderLock);
}
static void KeyEvent(uint32_t Key,int Down) {
    int Was,Character=0;
    if(Key>=256) return;
    Was=State.Keys[Key]; State.Keys[Key]=(uint8_t)Down;
    if(!Down || Was) return;
    if(Key==0x42) {
        State.EditMode=!State.EditMode; State.Dragging=0;
        if(State.EditMode) BeginEditing();
        return;
    }
    if(!State.EditMode) return;
    if(Key>=2 && Key<=11) Character="1234567890"[Key-2];
    else switch(Key) {
        case 0x1e:Character='a';break;case 0x30:Character='b';break;
        case 0x2e:Character='c';break;case 0x20:Character='d';break;
        case 0x12:Character='e';break;case 0x21:Character='f';break;
        case 0x26:Character='l';break;case 0x2d:Character='x';break;
        case 0x33:Character=',';break;case 0x27:Character=';';break;
        case 0x39:Character=' ';break;case 0x1c:Character=13;break;
        case 0x0e:Character=8;break;
    }
    if(Character) Queue(4,Character,Modifiers());
}
static int MouseEvent(int X,int Y,int Wheel,int Down) {
    int Flags=Modifiers(),Was=State.MouseDown,Owned;
    State.MouseDown=(uint8_t)Down;
    if(ReadCursor(1)) { /* Native cursor and hit testing share one position. */ }
    else if(!State.EditMode && !State.Dragging) { ReadCursor(0); }
    else {
        State.MouseX=Clamp(State.MouseX+X,(int)State.LastWidth-1);
        State.MouseY=Clamp(State.MouseY+Y,(int)State.LastHeight-1);
    }
    Owned=State.EditMode || State.Dragging || ((Flags&1) && Inside(State.MouseX,State.MouseY));
    if(!Owned) return 0;
    Queue(0,0,Flags);
    if(Down && !Was) { State.Dragging=1; Queue(1,0,Flags); }
    if(!Down && Was) { State.Dragging=0; Queue(2,0,Flags); }
    if(Wheel) Queue(3,Wheel,Flags);
    return 1;
}
void FilterDeviceState(void *Object,uint32_t Size,uint8_t *Data) {
    InputDevice *Device; uint32_t I; int Owned;
    if(!Data) return;
    if(!TryLock(&RenderLock)) {
        Device=FindInput(Object);
        if(Device && Size==Device->Size && InputClaimed()) memset(Data,0,Size);
        return;
    }
    Device=FindInput(Object);
    if(!Device || Size!=Device->Size || !InputActive()) goto Done;
    if(Device->Kind==2) {
        State.DirectKeyboard=1;
        Owned=State.EditMode;
        for(I=0;I<256;I++) KeyEvent(I,(Data[I]&0x80)!=0);
        if(Owned || State.EditMode) memset(Data,0,Size);
        else Data[0x42]=0; /* F8 belongs to this opt-in addon. */
    } else {
        State.DirectMouse=1;
        if(MouseEvent(*(int32_t *)Data,*(int32_t *)(Data+4),*(int32_t *)(Data+8),(Data[12]&0x80)!=0)) memset(Data,0,Size);
    }
Done: Unlock(&RenderLock);
}
void FilterDeviceData(void *Object,uint32_t Stride,uint8_t *Data,uint32_t *Count,uint32_t Capacity,uint32_t Flags) {
    InputDevice *Device; uint32_t I,Offset,Value; int Owned,X=0,Y=0,Wheel=0,Down;
    if(!Data || !Count || (Stride!=16 && Stride!=24) || *Count>Capacity || *Count>256) return;
    if((Flags&1) || !TryLock(&RenderLock)) {
        Device=FindInput(Object);
        if(Device && Device->Size && InputClaimed()) *Count=0;
        return; /* Peeking must not duplicate queued overlay events. */
    }
    Device=FindInput(Object);
    if(!Device || !Device->Size || !InputActive()) goto Done;
    Owned=State.EditMode || State.Dragging; Down=State.MouseDown;
    for(I=0;I<*Count;I++) {
        Offset=*(uint32_t *)(Data+I*Stride); Value=*(uint32_t *)(Data+I*Stride+4);
        if(Device->Kind==2) { State.DirectKeyboard=1; KeyEvent(Offset,(Value&0x80)!=0); }
        else {
            State.DirectMouse=1;
            if(Offset==0) X+=(int32_t)Value; else if(Offset==4) Y+=(int32_t)Value;
            else if(Offset==8) Wheel+=(int32_t)Value; else if(Offset==12) Down=(Value&0x80)!=0;
        }
    }
    if(Device->Kind==1) Owned|=MouseEvent(X,Y,Wheel,Down);
    if(Owned || State.EditMode) *Count=0;
Done: Unlock(&RenderLock);
}
intptr_t WindowMessage(void *Window,uint32_t Message,uintptr_t WParam,intptr_t LParam)
{
    void *Previous=State.PreviousProcedure;
    int Consume=0,Flags=Modifiers();
    if(TryLock(&RenderLock)) {
        if(Window==State.Window && ForegroundWindow()==Window && State.Enabled && ClockMilliseconds()-State.LastPublish<4000) {
            if(!State.DirectKeyboard && Message==0x100 && WParam==0x77 && !(LParam&0x40000000)) {
                State.EditMode=!State.EditMode; State.Dragging=0;
                if(State.EditMode) BeginEditing();
                Consume=1;
            } else if(Message==0x101 && WParam==0x77) Consume=1;
            else if(State.EditMode || (Flags&1)) {
                if(!ReadCursor(1) && !State.EditMode && !State.Dragging) ReadCursor(0);
                if(Message==0xff && (State.EditMode || State.Dragging || Inside(State.MouseX,State.MouseY))) {
                    uint8_t Raw[64]; uint32_t Size=sizeof(Raw);
                    if(State.DirectMouse) { Consume=1; goto Handled; }
                    if(ReadRawInput((void *)LParam,0x10000003,Raw,&Size,24)!=0xffffffff && Size>=48 && *(uint32_t *)Raw==0) {
                        uint16_t Buttons=*(uint16_t *)(Raw+28), MotionFlags=*(uint16_t *)(Raw+24);
                        if(!ReadCursor(1) && !(MotionFlags&1)) {
                            State.MouseX=Clamp(State.MouseX+*(int32_t *)(Raw+36),(int)State.LastWidth-1);
                            State.MouseY=Clamp(State.MouseY+*(int32_t *)(Raw+40),(int)State.LastHeight-1);
                        }
                        Queue(0,0,Flags);
                        if(Buttons&1) { State.Dragging=1; Queue(1,0,Flags); }
                        if(Buttons&2) { State.Dragging=0; Queue(2,0,Flags); }
                        if(Buttons&0x400) Queue(3,(int16_t)*(uint16_t *)(Raw+30),Flags);
                    }
                    Consume=1;
                } else if(Message==0x102 && State.EditMode) { if(!State.DirectKeyboard) Queue(4,(int)WParam,Flags); Consume=1; }
                else if((Message>=0x100 && Message<=0x109) && State.EditMode) Consume=1;
                /* Raw input is the only mouse event source, avoiding duplicate
                 * legacy clicks generated by the same device. */
                else if(Message>=0x200 && Message<=0x20e && (State.EditMode || State.Dragging || Inside(State.MouseX,State.MouseY))) Consume=1;
            }
        } else if(Message==8 || Message==0x82) { State.EditMode=State.Dragging=0; }
Handled: Unlock(&RenderLock);
    } else if(Window==State.Window && InputClaimed()) {
        Consume=Message==0xff || (Message>=0x200 && Message<=0x20e) ||
            (Message>=0x100 && Message<=0x109);
    }
    if(Consume) {
        if(Message==0xff) return DefaultWindowProcedure(Window,Message,WParam,LParam); /* native raw-input cleanup */
        return 0;
    }
    return Previous?CallWindowProcedure(Previous,Window,Message,WParam,LParam):DefaultWindowProcedure(Window,Message,WParam,LParam);
}
int InstallWindow(void *Window)
{
    if(!Window || !TryLock(&RenderLock)) return 0;
    if(State.Window!=Window) {
        if(State.Window && State.PreviousProcedure) SetWindowProcedure(State.Window,-4,(intptr_t)State.PreviousProcedure);
        State.PreviousProcedure=(void *)SetWindowProcedure(Window,-4,(intptr_t)WindowMessage);
        State.Window=State.PreviousProcedure?Window:0;
    }
    Unlock(&RenderLock); return State.Window!=0;
}
int InputSnapshot(int32_t *Output)
{
    int I,Count;
    if(!TryLock(&RenderLock)) return -1;
    Output[0]=(int)State.LastWidth; Output[1]=(int)State.LastHeight;
    /* bit0 controls editing; bit1 requests the fallback software cursor. */
    Output[2]=InputActive() && (State.EditMode || (State.Dragging && (Modifiers()&1)));
    if(Output[2] && !ReadCursor(1)) Output[2]|=2;
    Output[3]=State.MouseX; Output[4]=State.MouseY;
    Count=State.InputCount;
    for(I=0;I<Count;I++) memcpy(Output+5+I*5,&State.Events[(State.InputHead+I)%INPUTS],sizeof(InputEvent));
    State.InputHead=State.InputCount=0; Unlock(&RenderLock); return Count;
}
int InputStatus(uint32_t *Output) {
    int I;
    if(!TryLock(&RenderLock)) return 0;
    Output[0]=State.Window!=0; Output[1]=State.DirectMouse; Output[2]=State.DirectKeyboard;
    Output[3]=State.EditMode; Output[4]=Output[5]=0;
    for(I=0;I<8;I++) { if(State.Devices[I].Object) Output[4]++; if(State.Devices[I].Size) Output[5]++; }
    Unlock(&RenderLock); return 1;
}
#ifndef OVERLAY_TEST
typedef struct { void *Object; uint8_t *Data; uint32_t Size,*Count,Capacity,Flags; } InputCall;
void OnInputStateEnter(GumInvocationContext *Context) {
    InputCall *Call=gum_invocation_context_get_listener_invocation_data(Context,sizeof(InputCall));
    Call->Object=gum_invocation_context_get_nth_argument(Context,0);
    Call->Size=(uint32_t)(uintptr_t)gum_invocation_context_get_nth_argument(Context,1);
    Call->Data=gum_invocation_context_get_nth_argument(Context,2);
}
void OnInputStateLeave(GumInvocationContext *Context) {
    InputCall *Call=gum_invocation_context_get_listener_invocation_data(Context,sizeof(InputCall));
    if((int32_t)(intptr_t)gum_invocation_context_get_return_value(Context)>=0) FilterDeviceState(Call->Object,Call->Size,Call->Data);
}
void OnInputDataEnter(GumInvocationContext *Context) {
    InputCall *Call=gum_invocation_context_get_listener_invocation_data(Context,sizeof(InputCall));
    Call->Object=gum_invocation_context_get_nth_argument(Context,0);
    Call->Size=(uint32_t)(uintptr_t)gum_invocation_context_get_nth_argument(Context,1);
    Call->Data=gum_invocation_context_get_nth_argument(Context,2);
    Call->Count=gum_invocation_context_get_nth_argument(Context,3);
    Call->Capacity=Call->Count?*Call->Count:0;
    Call->Flags=(uint32_t)(uintptr_t)gum_invocation_context_get_nth_argument(Context,4);
}
void OnInputDataLeave(GumInvocationContext *Context) {
    InputCall *Call=gum_invocation_context_get_listener_invocation_data(Context,sizeof(InputCall));
    if((int32_t)(intptr_t)gum_invocation_context_get_return_value(Context)>=0) FilterDeviceData(Call->Object,Call->Size,Call->Data,Call->Count,Call->Capacity,Call->Flags);
}
void OnInputFormatEnter(GumInvocationContext *Context) {
    InputCall *Call=gum_invocation_context_get_listener_invocation_data(Context,sizeof(InputCall));
    Call->Object=gum_invocation_context_get_nth_argument(Context,0);
    Call->Data=gum_invocation_context_get_nth_argument(Context,1);
}
void OnInputFormatLeave(GumInvocationContext *Context) {
    InputCall *Call=gum_invocation_context_get_listener_invocation_data(Context,sizeof(InputCall));
    if((int32_t)(intptr_t)gum_invocation_context_get_return_value(Context)>=0) InputFormat(Call->Object,Call->Data);
}
void OnPresent(GumInvocationContext *Context) {
    Draw(gum_invocation_context_get_nth_argument(Context,0),(uint32_t)(uintptr_t)gum_invocation_context_get_nth_argument(Context,2),0);
}
void OnPresent1(GumInvocationContext *Context) {
    const uint8_t *Params=gum_invocation_context_get_nth_argument(Context,3);
    int Partial=Params && (*(const uint32_t *)Params || *(void *const *)(Params+16) || *(void *const *)(Params+24));
    Draw(gum_invocation_context_get_nth_argument(Context,0),(uint32_t)(uintptr_t)gum_invocation_context_get_nth_argument(Context,2),Partial);
}
#endif
