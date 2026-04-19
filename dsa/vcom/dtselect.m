function [Out1]=dtselect(Action, In1, In2)
% function [Out1]=dtselect(Action, In1, In2)
% dtselect = DataTypeSelect dialog which
% lets user select which data type he wants
% transferred to Excel. This dialog is called by
% toexcel.m
% KDS

iFig=100;

iRBtime=1;
iRBxfer=2;
iRBaspec=3;
iRBcspec=4;
iRBacor=5;
iRBxcor=6;
iRBcoh=7;
iRBfft=8;
iRBimp=9;
iRBvca=10;

iTXTtime=11;
iTXTxfer=12;
iTXTaspec=13;
iTXTcspec=14;
iTXTacor=15;
iTXTxcor=16;
iTXTcoh=17;
iTXTfft=18;
iTXTimp=19;
iTXTvca=20;

iSend2Excel=22;
iWrite2Text=23;

strings = {'Capture Buffer','Impulse Response','Complex FFT',...
   'Coherence','Cross-Correlation','Auto-Correlation',...
   'Cross-Spectrum','Auto-Spectrum','Transfer Function',...
   'Time Series'};

global hDTSelect;

if nargin == 0
   Action = 'init';
end;

switch Action
case 'init'
   if exist('dtselect.mat')
       load dtselect;
   else
       dtselectpos = [10 150 160 330];
   end;
   
   % create figure
   hDTSelect(iFig) = figure('Color',[0 .5 .5],...
                       'Units','pixels',...
                       'Interruptible','off',...
                       'Position',pos_clip(dtselectpos),...
                       'MenuBar','none',...
                       'NumberTitle','off',...
                       'resize','off',...
                       'renderer','painters',... 
                       'closerequest','dtselect(''close'')',...
                       'backingstore','off',...
                       'Name',['Export'],...
                       'Tag','siglab_dtselect',...
                       'Visible','on',...
                       'userdata',[]);
   
   pbx = 10;
   pby = 10;
   pbw = 140;
   pbh = 30;
   hDTSelect(iSend2Excel) = uicontrol('style','pushbutton',...
                       'string','Send to Excel',...
                       'enable','off',...
                       'position',[pbx pby-5 pbw pbh],...
                       'callback','dtselect(''export'',''Plot in Excel'');'); 
      
   hDTSelect(iWrite2Text) = uicontrol('style','pushbutton',...
                       'string','SaveAs to Text',...
                       'enable','off',...
                       'position',[pbx pby+pbh pbw pbh],...
                       'callback','dtselect(''export'',''Save As to Text'');');
   x0 = 10;
   y0 = pby+pbh+15;
   yd = 25;
   rbh = 20;
   rbw = 20;
   sp=0;
   for i = iRBvca:-1:iRBtime
       if i>=8
           onoff = 'on';
       else
           onoff = 'off';
       end;
       % for the text objects, the callback doesn't execute?
       hDTSelect(i) = uicontrol('style','text',...
                       'enable',onoff,...
                       'position',[x0+rbw+sp y0+i*yd 120 20],...
                       'buttondownfcn',['dtselect(''txt'',',num2str(i),');'],...
                       'string',strings{i});
       hDTSelect(i) = uicontrol('style','radio',...
                       'enable',onoff,...
                       'callback',['dtselect(''rb'',',num2str(i),');'],...
                       'position',[x0 y0+i*yd rbw rbh]);
   end;      
case 'export'
   for i = iRBtime:iRBvca
       selected = get(hDTSelect(i),'value');
       if selected
           toexcel(strings{i},In1);
           break;
           dtselect('close');
       end;
   end;
  
case 'rb'
   selected = get(hDTSelect(In1),'value');
   if selected
       set(hDTSelect(iSend2Excel),'enable','on');
       set(hDTSelect(iWrite2Text),'enable','on');
       for i = iRBtime:In1-1
           set(hDTSelect(i),'value',0); 
       end;
       for i = In1+1:iRBvca
           set(hDTSelect(i),'value',0); 
       end;      
   else
       set(hDTSelect(iSend2Excel),'enable','off');
       set(hDTSelect(iWrite2Text),'enable','off');
   end;

case 'txt'
   selected = get(hDTSelect(iRBtime+In1),'value')
   if ~selected
       set(hDTSelect(iRBtime+In1),'value',1);
       dtselect('rb',iRBtime+In1);
   else
       set(hDTSelect(iRBtime+In1),'value',0);        
   end;

case 'close'
   dtselectpos=get(gcbf,'position');
   [drv pth]=pathfind('vcom');
   eval(['save ',drv,pth,'\dtselect dtselectpos'],['error([''Could not save position''])']); 
   set(hDTSelect(iFig),'closereq','closereq');
   delete(gcbf);
otherwise
   disp(['Action unrecognized: ', Action]);   
end; % end main case