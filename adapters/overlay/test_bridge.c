/* Critical ABI/ownership checks with mock COM. No claim of GPU/input parity. */
#define OVERLAY_TEST
#include "bridge.c"
#include <assert.h>
#include <stdio.h>
OverlayState State;
uint8_t RenderLock;
static uint64_t Now=100;
static int Busy,Executed,Restored,Created,FailFinish;
typedef struct { void **Table; int References; } Object;
static void *BufferTable[11],*DeviceTable[41],*ContextTable[115],*ChainTable[13];
static Object Buffer={BufferTable,0},Device={DeviceTable,0},Context={ContextTable,0},Deferred={ContextTable,0};
static Object Chain={ChainTable,1},Resources[64];
static TextureDescription Description={1920,1080,1,1,28,1,0,0,0,0,0};
int TryLock(void *Lock) { (void)Lock; if(Busy) return 0; Busy=1; return 1; }
void Unlock(void *Lock) { (void)Lock; assert(Busy); Busy=0; }
uint64_t ClockMilliseconds(void) { return Now; }
int16_t KeyState(int Key) { (void)Key; return 0; }
void *ForegroundWindow(void) { return State.Window; }
intptr_t SetWindowProcedure(void *W,int I,intptr_t P) { (void)W;(void)I;(void)P;return 0; }
intptr_t CallWindowProcedure(void *P,void *W,uint32_t M,uintptr_t A,intptr_t B) { (void)P;(void)W;(void)M;(void)A;(void)B;return 77; }
intptr_t DefaultWindowProcedure(void *W,uint32_t M,uintptr_t A,intptr_t B) { (void)W;(void)M;(void)A;(void)B;return 0; }
uint32_t ReadRawInput(void *H,uint32_t C,void *D,uint32_t *S,uint32_t Z) { (void)H;(void)C;(void)D;(void)S;(void)Z;return 0xffffffff; }
static int NativeCursor;
static Point NativePosition,WindowOrigin;
static Rectangle WindowBounds;
int CursorPosition(Point *P) { if(!NativeCursor) return 0; *P=NativePosition;return 1; }
int ClientPosition(void *W,Point *P) { (void)W; P->X-=WindowOrigin.X; P->Y-=WindowOrigin.Y;return 1; }
int ClientBounds(void *W,Rectangle *R) { (void)W; *R=WindowBounds;return 1; }
int CursorInformation(CursorDescription *D) {
    assert(D->Size==sizeof(CursorDescription)); D->Flags=NativeCursor?1:0;
    D->Position=NativePosition;return 1;
}
static uint32_t Drop(void *Self) { Object *Value=Self; assert(Value->References>0); return --Value->References; }
static void New(void **Out) { assert(Created<64); Resources[Created].Table=BufferTable; Resources[Created].References=1; *Out=&Resources[Created++]; }
static int32_t GetBuffer(void *Self,uint32_t Index,const Guid *Id,void **Out)
{ (void)Self; assert(Index==0 && Id->A==TextureId.A); Buffer.References++; *Out=&Buffer; return 0; }
static int32_t GetDevice(void *Self,const Guid *Id,void **Out)
{ (void)Self; assert(Id->A==DeviceId.A); Device.References++; *Out=&Device; return 0; }
static void Describe(void *Self,TextureDescription *Out) { assert(Self==&Buffer); *Out=Description; }
static void Immediate(void *Self,void **Out) { assert(Self==&Device); Context.References++; *Out=&Context; }
static int32_t MakeDeferred(void *Self,uint32_t Flags,void **Out)
{ assert(Self==&Device && !Flags); Deferred.References++; *Out=&Deferred; return 0; }
static int32_t Texture(void *Self,const TextureDescription *D,const void *Initial,void **Out)
{ assert(Self==&Device && !Initial && D->Format==28 && D->Bind==8); New(Out); return 0; }
static int32_t View(void *Self,void *Source,const void *Desc,void **Out)
{ assert(Self==&Device && Source && !Desc); New(Out); return 0; }
static int32_t Shader(void *Self,const void *Bytes,uintptr_t Size,void *Linkage,void **Out)
{ assert(Self==&Device && Bytes && Size==4 && !Linkage); New(Out); return 0; }
static int32_t Blend(void *Self,const void *Desc,void **Out)
{ const uint32_t *D=Desc; assert(Self==&Device && D[2]==1 && D[3]==5 && D[4]==6 && D[6]==2 && D[9]==15); New(Out); return 0; }
static int32_t Raster(void *Self,const void *Desc,void **Out)
{ const uint32_t *D=Desc; assert(Self==&Device && D[0]==3 && D[1]==1 && D[6]==1); New(Out); return 0; }
static void Upload(void *Self,void *T,uint32_t S,const void *B,const void *P,uint32_t Pitch,uint32_t Depth)
{ assert(Self==&Context && T && !S && !B && P && Pitch>0 && !Depth); }
static void BindShader(void *Self,void *S,void **Classes,uint32_t Count) { assert(Self==&Deferred && S && !Classes && !Count); }
static void Array(void *Self,uint32_t Start,uint32_t Count,void **Views) { assert(Self==&Deferred && !Start && Count==1 && *Views); }
static void Targets(void *Self,uint32_t Count,void **Views,void *Depth) { assert(Self==&Deferred && Count==1 && *Views && !Depth); }
static void BindBlend(void *Self,void *B,const float *F,uint32_t Mask) { assert(Self==&Deferred && B && !F && Mask==0xffffffff); }
static void BindRaster(void *Self,void *R) { assert(Self==&Deferred && R); }
static void Topology(void *Self,uint32_t T) { assert(Self==&Deferred && T==4); }
static void SetViewport(void *Self,uint32_t Count,const Viewport *V) { assert(Self==&Deferred && Count==1 && V->Width>0 && V->Height>0); }
static void Triangle(void *Self,uint32_t Count,uint32_t Start) { assert(Self==&Deferred && Count==3 && !Start); }
static int32_t Finish(void *Self,int Restore,void **Out) { assert(Self==&Deferred && !Restore); if(FailFinish) return -1; New(Out); return 0; }
static void Execute(void *Self,void *Commands,int Restore) { assert(Self==&Context && Commands && Restore); Executed++; Restored++; }
static void InputChecks(void) {
    uint32_t I,Mouse[5]={0},Count; uint8_t Keyboard[256]={0};
    uint64_t Format[4]={0},Objects[256*3]={0},Buffered[3]={0};
    uint8_t *F=(uint8_t *)Format,*O=(uint8_t *)Objects;
    State.Window=(void *)1; State.Enabled=1; State.LastPublish=Now;
    assert(RegisterInput((void *)2,2) && RegisterInput((void *)3,1));
    *(uint32_t *)F=32; *(uint32_t *)(F+4)=24; *(uint32_t *)(F+8)=2;
    *(uint32_t *)(F+12)=256; *(uint32_t *)(F+16)=256; *(void **)(F+24)=O;
    for(I=0;I<256;I++) *(uint32_t *)(O+I*24+8)=I;
    InputFormat((void *)2,F); assert(FindInput((void *)2)->Size==256);
    State.MouseX=160; State.MouseY=100;
    Keyboard[0x42]=128; FilterDeviceState((void *)2,256,Keyboard);
    assert(State.EditMode && Keyboard[0x42]==0);
    Keyboard[0x42]=128; FilterDeviceState((void *)2,256,Keyboard); assert(State.EditMode); /* held F8 cannot repeat */
    *(uint32_t *)(F+12)=20; *(uint32_t *)(F+16)=11;
    for(I=0;I<11;I++) *(uint32_t *)(O+I*24+8)=I<3?I*4:I+9;
    InputFormat((void *)3,F); assert(FindInput((void *)3)->Size==20);
    Mouse[0]=10; Mouse[1]=5; Mouse[3]=128;
    FilterDeviceState((void *)3,20,(uint8_t *)Mouse);
    assert(State.MouseX==170 && State.MouseY==105 && State.Dragging && Mouse[0]==0 && Mouse[3]==0);
    *(uint32_t *)Buffered=12; *(uint32_t *)((uint8_t *)Buffered+4)=128; Count=1;
    FilterDeviceData((void *)3,24,(uint8_t *)Buffered,&Count,1,0); assert(Count==0);
    State.EditMode=State.Dragging=0; Mouse[0]=17; Mouse[3]=0;
    FilterDeviceState((void *)3,20,(uint8_t *)Mouse); assert(Mouse[0]==17); /* normal game input untouched */
    State.EditMode=1; Mouse[0]=19; State.LastPublish=Now-5000;
    FilterDeviceState((void *)3,20,(uint8_t *)Mouse); assert(Mouse[0]==19); /* abandoned overlay cannot lock input */
    State.LastPublish=Now; State.PreviousProcedure=(void *)1;
    Busy=1; Mouse[2]=120;
    FilterDeviceState((void *)3,20,(uint8_t *)Mouse); assert(Mouse[2]==0);
    *(uint32_t *)Buffered=8; Count=1;
    FilterDeviceData((void *)3,24,(uint8_t *)Buffered,&Count,1,0); assert(Count==0);
    assert(WindowMessage(State.Window,0x20a,120<<16,0)==0); /* wheel cannot switch weapons */
    State.EditMode=State.Dragging=0;
    assert(WindowMessage(State.Window,0x20a,120<<16,0)==77); /* normal wheel passes */
    Busy=0; State.EditMode=1;
    State.LastPublish=Now; *(uint32_t *)(O+8)=99; InputFormat((void *)3,F);
    assert(FindInput((void *)3)->Size==0); /* custom format rejected */
    /* Window on the left monitor, render target larger than client pixels. */
    NativeCursor=1; WindowOrigin=(Point){-1920,100}; WindowBounds=(Rectangle){0,0,1280,720};
    NativePosition=(Point){-1280,460}; State.LastWidth=2560; State.LastHeight=1440;
    BeginEditing(); assert(State.MouseX==1280 && State.MouseY==720);
    MouseEvent(999,999,0,0); assert(State.MouseX==1280 && State.MouseY==720);
    { int32_t Snapshot[325]; State.EditMode=1; InputSnapshot(Snapshot);
      assert(Snapshot[2]==1); /* native cursor only, no duplicate */
      NativePosition.X=100; ReadCursor(1); assert(State.MouseX>2560); /* other monitor */
      NativeCursor=0; InputSnapshot(Snapshot); assert(Snapshot[2]==3); /* hidden-mode fallback */
    }
    State.EditMode=State.Dragging=0; State.Window=0;
}
int main(void)
{
    static uint8_t Pixels[400]; uint32_t Code=1; int I; int32_t Input[325];
    BufferTable[2]=DeviceTable[2]=ContextTable[2]=(void *)Drop; BufferTable[10]=(void *)Describe;
    DeviceTable[5]=(void *)Texture; DeviceTable[7]=DeviceTable[9]=(void *)View;
    DeviceTable[12]=DeviceTable[15]=(void *)Shader; DeviceTable[20]=(void *)Blend; DeviceTable[22]=(void *)Raster;
    DeviceTable[27]=(void *)MakeDeferred; DeviceTable[40]=(void *)Immediate;
    ContextTable[8]=(void *)Array; ContextTable[9]=ContextTable[11]=(void *)BindShader;
    ContextTable[13]=(void *)Triangle; ContextTable[24]=(void *)Topology; ContextTable[33]=(void *)Targets;
    ContextTable[35]=(void *)BindBlend; ContextTable[43]=(void *)BindRaster; ContextTable[44]=(void *)SetViewport;
    ContextTable[48]=(void *)Upload; ContextTable[58]=(void *)Execute; ContextTable[114]=(void *)Finish;
    ChainTable[7]=(void *)GetDevice; ChainTable[9]=(void *)GetBuffer;
    assert(sizeof(State)<16*1024*1024 && sizeof(TextureDescription)==44);
    assert(Shaders(&Code,4,&Code,4));
    assert(!Publish(3,0,0,10,10,Pixels,400)); assert(!Publish(0,0,0,1201,1,Pixels,4804));
    assert(!Publish(0,0,0,10,10,Pixels,399)); assert(!Publish(0,0,0,0,10,Pixels,0));
    assert(Publish(0,16,16,10,10,Pixels,400)); Draw(&Chain,0,0);
    assert(Executed==1 && Restored==1 && !Buffer.References && Device.References==1 && Context.References==1);
    Description.Width=1280; Draw(&Chain,0,0); assert(Executed==2 && !Buffer.References);
    Draw(&Chain,1,0); Draw(&Chain,0,1); Description.Samples=4; Draw(&Chain,0,0); assert(Executed==2 && !Buffer.References);
    Description.Samples=1; Description.Format=10; Draw(&Chain,0,0); assert(Executed==2);
    Description.Format=87; Draw(&Chain,0,0); assert(Executed==3);
    Busy=1; Draw(&Chain,0,0); assert(!Publish(0,0,0,10,10,Pixels,400)); Busy=0;
    Now+=4001; Draw(&Chain,0,0); assert(Executed==3);
    Heartbeat(); Draw(&Chain,0,0); assert(Executed==4);
    for(I=0;I<100;I++) Queue(4,I,0);
    assert(InputSnapshot(Input)==64 && Input[8]==36 && InputSnapshot(Input)==0);
    InputChecks();
    FailFinish=1; Draw(&Chain,0,0); assert(State.Errors==1 && !State.Device && !Buffer.References);
    Stop();
    assert(!Device.References && !Context.References && !Deferred.References);
    for(I=0;I<Created;I++) assert(!Resources[I].References);
    puts("PASS overlay: frame bounds, RGBA blending, state restoration, backbuffer lifetime, resize, expiry, input bounds, failure cleanup");
}
