  function Out1=ovldstat(Action,In1,In2,In3,In4)
% function Out1=ovldstat(Action,In1,In2,In3,In4)
% This is a Spartan version of its predecessor. Pixels are precious.
% Actions 
%  'init'        create all leds, but leave hidden. 
%                In1 pos, In2 number of actual channels in system, In3 color
%                In4 (optional) parent figure handle
%  'clear'       clear globals
%  'show'        show leds
%  'hide'        hide all objects 
%  'reconfig'    update active/off of all leds (Off = LBL_BKc color)
%  'set'         In1 = channel #, In2 On/Off;
%  'get'
%     'status'   return status for each channel in In1
%  'process'     overload information In1=clist, In2=ovld, (optional) In3= new overload info
% Dick Benson , DSP Technology    

%  GRN = ok    & active
%  RED = ovld  & active
%  YEL = ovld  & not active
%  BKG = ok    & not active

%  
%define
   %  in each led, userdata represents the following:
   %OK_ACTc    =  0; % ok    & active
   %OVLD_ACTc  =  1; % ovld  & active
   %OVLD_NACTc = -1; % ovld  & not active
   %OK_NACTc   = -2; % ok    & not active 
   
   % basic colors , nothing fancy
   %REDc       = [1,0,0];
   %YELc       = [1,1,0];
   %GRNc       = [0,1,0];

   % index def
   %igroup     = 1;
   %ilabel     = 2;
   %iledbase   = 2; % base @ last element
   
   % dimensions
   %wled1       = 7;  % for <= 8 
   %wled2       = 5;  % for >  8 channels  
  
   %dx1         = 12; % for <= 8 
   %dx2         = 7;  % for >  8 channels 
   %hled        = 14;
   %htxt        = 15;
   %wtxt        = 30; % 40 
%end_define

   global HST_;      % array of handles
   
if strcmp(Action,'init')
   %INIT
   pos       = In1;  % x,y position in pixels
   chans     = In2;
                     %  In3 ignored was color info, but this is not really necessary
                     
   if nargin ==5
      hf = In4;
   else
      hf = gcf;
   end;
                     
   % tighten up spacing for > 8 channels so as to leave room for other objects               
   if chans <= 8 
       dx = 12;
       wl = 7;
   else 
       dx = 7;
       wl = 5;
   end;                   
   
   HST_(1)=  uicontrol(hf,'Style','frame','visible','on',...
                                    'Position',[pos,[4+30+(chans)*dx,14+4]],...
                                    'BackGroundColor',(192/255)*[1,1,1],...
                                    'UserData',[]);
                                    
   HST_(2)=  uicontrol(hf,'Style','text','visible','on',...
                                    'Position',[pos+[2,2],30,15],...
                                    'string','Ovld:',...
                                    'BackGroundColor',(192/255)*[1,1,1],...
                                    'UserData',chans);
   for i=1:chans
       HST_(2+i) = uicontrol(hf,'Style','frame','visible','off',...
                                    'Position',[pos+[4+30+(i-1)*(dx),2],wl,14],...
                                    'BackGroundColor',(192/255)*[1,1,1],...
                                    'UserData',[]); 
   end; 
   
   set(HST_([(2+1):(2+chans)]),'visible','on');
       

elseif strcmp(Action,'proc')
    % PROCESS overload chanels in In2 and possibly In3 
   if nargin ==4 
      process = 0; 
      % new method .... 9/6/96 do a better job of comparing old/new overload info
      % In3 has new overload information
      % In2 has old overload information
      if length(In3)~=length(In2)
         % the lengths are different therefore a change has occured
         process = 1;
      else
         process = sum(In3~=In2);  % same length, detect any changes
      end;
      In2  = In3; % only will take effect if process>=1
      Out1 = In3; % update overload state of calling routine
   else
      process = 1;  % 3 input argument calling sequence ... 
                    % unconditionally process the ovld info contained in In2
   end;
   
   if process
      bkcolor = get(HST_(2),'BackGroundColor'); 
      clist = zeros(1,abs(get(HST_(2),'UserData'))); 
      olist = clist;
      if ~isempty(In2)
         if In2==0 In2=[]; end;  % 0 indicates no overloads
      end;   
      clist(In1) = ones(size(In1));
      olist(In2) = ones(size(In2));
      set(HST_(find( olist& clist)+2),'BackGroundColor',[1,0,0],'UserData',1);
      set(HST_(find( olist&~clist)+2),'BackGroundColor',[1,1,0],'UserData',-1);
      set(HST_(find(~olist& clist)+2),'BackGroundColor',[0,1,0],'UserData',0);
      set(HST_(find(~olist&~clist)+2),'BackGroundColor',bkcolor,'UserData',-2);
   end;

elseif strcmp(Action,'set'),
%SET
   if strcmp(In2,'on'),
      set(HST_(In1+2),'BackGroundColor',[0,1,0],'UserData',1); 
   else
      color = get(HST_(2),'BackGroundColor');
      set(HST_(In1+2),'BackGroundColor',color,'UserData',0);
   end;

elseif strcmp(Action,'hide'),
%HIDE
    disp(['no hide yet in ovldstat.m']);
elseif strcmp(Action,'update'), 
    disp(['no update yet in ovldstat.m']);
    
elseif strcmp(Action,'reconfig'),
%RECONFIG
% In1 has clist which contains active channels
  bkcolor = get(HST_(2),'BackGroundColor');
  for i=1:(length(HST_)-(2)),
       mf = 0;      % assume not a member
       for j=1:length(In1),
           if i==In1(j), mf=1; end; 
       end;
        if HST_(i+2) > 0, 
          if mf == 1, 
            set(HST_(i+2),'BackGroundColor',[0,1,0],'UserData',0); 
          else 
            set(HST_(i+2),'BackGroundColor',bkcolor,'UserData',-2); 
          end; 
        end;
  end;

elseif strcmp(Action,'get')
% GET
    if strcmp(In1,'status'),
    % In2 has vector of active channels 
       Out1 = zeros(length(In2),1); 
       for i=1:length(In2), 
         Out1(i)=get(HST_(In2(i)+(2)),'UserData'); 
       end; 
    else
       disp([In1,' not recognized in ovldstat(get,xxx)']);
    end;
    
elseif strcmp(Action,'clear')
% CLEAR
    clear global HST_
else
   disp(['Action:',Action,' not recognized in ovldstat.m']); 
end; 
% end function 












