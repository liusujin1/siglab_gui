  function uiyncf(dialogname,xypos,info,cbYes,cbNo,cbCancel)
% function uiyncf(dialogname,xypos,info,cbYes,cbNo,cbCancel)
% Yes / No / Cancel dialog
% Ideas & modal.dll from MathWorks Modal Dialog distribution 
% Dick Benson, DSP Technology

     hf=figure('numbertitle','off','resize','off','menu','none',...
               'pos',[xypos(1),xypos(2), 180,145],... 
               'color',get(0,'defaultuicontrolbackgroundcolor'),... 
               'name',dialogname,'BackingStore','off'); 

     if beyondv4
         set(hf,'WindowStyle','Modal');
         s = ['close(gcf); '];
     else
         ss = ['''',dialogname,''''];     % can you believe the quotes ????
         s  = [' modal(',ss,'); close(gcf); drawnow;'];  % close vs delete ?
              % the drawnow reduces (eliminates?) a nasty GPF that occures 
              % when running win95 and MATLAB 4.2c.1.1 
              % pure magic
     end;     
     % do the dialog cleanup b4 the user callback eg [s,cbYes]
     % rather than [cbYes,s] 
     
     % decide button size and placement
     sumf=0; yflg=0; nflg=0; cflg=0;
     
     if ~strcmp(cbYes,'')
        yflg=1;
     end;
     
     if ~strcmp(cbNo,'')
        nflg=1;
     end;

     if ~strcmp(cbCancel,'')
        cflg=1;
     end;
     
     sumf=yflg+nflg+cflg;
     if sumf==1
        posV=   [60,10,60,20];
     elseif sumf==2
        posV=  [[40,10,40,20];
                [100,10,40,20]]; 
     elseif sumf==3
        posV=  [[25,10,40,20];
                [70,10,40,20];
                [115,10,40,20]];
     else
        disp('error in uiyncf.m');
     end; 
     
     
     index=1;
     if yflg==1 
         h1=uicontrol('str','Yes','pos',posV(index,:),...
                      'callback',[s,cbYes]);
         index=index+1;
     end;
  
     if nflg==1 
         h2=uicontrol('str','No','pos' ,posV(index,:),...
                      'callback',[s,cbNo]);
         index=index+1;
     end;
     
     if cflg==1
         h3=uicontrol('str','Cncl','pos',posV(index,:),...
                      'callback',[s,cbCancel]); 
     end;
     
     h4=uicontrol('sty','text','pos',[10, 40, 160, 95],'str',info);
                 
     if beyondv4
        % nothing to do
     else
        % set the figure with dialogname to be modal
        modal(dialogname); 
     end;
% end function






















