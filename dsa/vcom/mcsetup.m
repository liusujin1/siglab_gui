function mcsetup(Action, In1, In2)
% mcsetup.mi
% A user interface for multi-channel setup
% It is called by vos, vsa, or vna from the "MC Setup" selection from 
% their respective figure menus.

%include
% vcol_h.m                                                   
%%%%%%%%%%%%%%%%%%%%%%%%% vdlg1_h.m 
%end_include

% function [Out1,Out2]=mcsetup(Action, In1, In2, In3)

STATE=1; FS=2; CUP=3; OFFSET=4; LABEL=5; EUVAL=6;
EU=7; PEREU= 8; DBREF=9; FIGURE=10; EUTOGGLE = 11;
APPLY=12; SETALL=13; UNDO=14; SAVEAS=15; REFCHAN=16;

% global that allow access to SigLab channel setup "state" 
global VDLG1_S1; 
global VDLG1_S2;
global HVDLG1_;    

% Everytime the mc setup user interface is called
% certain basic information is needed like uicontrol handles
% and a check to see if it's already present but invisible.
handle = findobj('tag','siglab_mc_setup');
if ~isempty(handle)
   gcf_ud = get(handle,'userdata');
   inchans = gcf_ud{1};
   mch = gcf_ud{2};
   ch = gcf_ud{3};
   if strcmp(Action,'init')
       set(handle,'visible','on');
       figure(handle);
       return;
   end
end

if isempty(Action) 
   Action = 'init';
   mch=ones(11); 
end

switch Action

case 'init'
     % setup the colors
     if exist('vi_color.mat','file')
        load vi_color.mat
        mc_color = stored_vi_colors;
       % currently not using uifont as fonts that look
       % pleasing in other vi's aren't necessarily
       % appropriate in this application.
       % passing 0 to uifont, uses the defaults which is bold
       SaveFont = uifont(0);	
     else
       mc_color = [[0,0,.25098];[0,0.50196,0.50196];[0.75294,0.75294,0.75294];[1,1,0];[0,0,0];[0,0,1];[0,1,0];[0,1,1];[1,1,0];[1,0,0];[0.25098,0,0];[0,1,0];[0.75294,0.75294,0.75294];[1,1,1];[1,1,1];[0,0,0];[0,0,0];[0,0,0];[0,0,0];[1,1,0];[1,0,0];[.3,.3,.3];[0.5, 1, 0];];   	
     end
   % code to convert VDLG1_S1 and VDLG1_S2 to an array of structures
   if isempty(VDLG1_S1)
       msgbox('global variables VDSG1_S1 is not set properly.',...
                   'SigLab Warning',...
                   'warn',...
                   'modal');
       return;
   end
   states    = VDLG1_S1(:,2);  % Channel Enable/Disable
   fss         = VDLG1_S1(:,1);
   cups      = VDLG1_S1(:,3);
   dbrefs    = VDLG1_S1(:,4);
   eustate  = VDLG1_S1(:,5);  % EU On/Off
   offsets   = VDLG1_S1(:,6);
   euvals    = VDLG1_S1(:,7);  % Sensitivity
   % CREATE STRUCTURES
   % Create a huge structure which holds the state of Siglab
   % channel setup. Frequency, processing, triggering, and
   % display are not controlled via this interface.
   % Currently structure elements naming conventions are in plural form 
   % (e.g. offsets not offset) since the lovely vdlg1_h.m/mpp 
   % combo enumerates on words like "offset" ;-)

   % mch is a vector of object handles
      
   % There are 10 properties associated with channel setup
   properties = {'states','fss','cups','offsets',...
                 'labels','euvals','eus','pereu','dbrefs'};
   eustates = struct('strings','','enables','','values',1);
   % ch struct holds the current state of the mc user interface.
   % This will be equivalent to the actual SigLab state after the
   % user presses the Apply button. At that time, the globals 
   % VDLGeez will take on the ch struct's values.
   % had to replace '' with [] for this to work correctly in MATLAB R12 (RAB)
   ch = struct('states',[],'fss',[],'cups',[],...
               'offsets',[],'labels','',...
               'euvals',[],'eus','','eustates',eustates,...
               'dbrefs',[]);
   inchans = In1; 
   for i = 1:inchans
      labels{i}=pullstr(VDLG1_S2(i,:),1);
      eus{i}=pullstr(VDLG1_S2(i,:),2); 
   end;
 % INIT STRUCTURE
   for i = 1:inchans
       if eustate(i) == 1
          ch.eustates(i).strings = '/Volt';
          ch.eustates(i).enables = 'on';
          ch.eustates(i).values = 1;
      else
          ch.eustates(i).strings = 'Off';
          ch.eustates(i).enables = 'off';
          ch.eustates(i).values = 0;
       end;    
   end;
   range = 1:inchans;
   ch.states(range)=[states(range)]; 
   ch.fss(range)=fss(range);
   ch.cups(range)=cups(range)+1;
   ch.offsets(range)=offsets(range);
   ch.euvals(range)=euvals(range);
   ch.dbrefs(range)=dbrefs(range);
   for i =1:inchans
       ch.labels{i}=labels{i};
       ch.eus{i}=eus{i};
   end;
   % variables for the physical layout
   vertical_spacing = 2;
   horizontal_spacing = 0;
   right_margin = 5;
   text_height = 16;
   columns = 9;
   text_columns = 7; 
   heights = [22,22,22,22,22,22,22,22,22]; 
   text_top = (inchans+1)*(heights(1)+vertical_spacing)+10;
   top = text_top+2;
   tops = [top-1,top,top,top,top,top,top,top,top];
   % 0 dB reference are not relevant to vos and vna. 
   % Therefore, we shrink the "non-resizable" mc figure window
   % such that the "0 dB reference" control is not visible.
   % This is easier than changing visibilities and/or enable status.
   [a, b, c, d, owner]=v_dlg1('get','state');
   if strcmp(owner,'vos') 
       shrink_factor = 83;
   elseif strcmp(owner,'vsa') || strcmp(owner,'vna')
       shrink_factor = 0;
   else
       error(['unrecognized owner = ',owner]);
       shrink_factor = 0; 
   end;

   if exist('mcsetpos.mat','file')
       load mcsetpos.mat;
       if inchans == mcsetpos{2}
           position = mcsetpos{1};
       else
           position = [50 50 639-shrink_factor text_top+22];
       end;
   else
       position = [50 50 639-shrink_factor text_top+22];
   end;
   % main figure window
   name = 'MC Setup';
   mch(FIGURE,1) = figure('Menu','none',...
                          'DefaultTextInterpreter','none',...
                          'Color',mc_color(1,:),...
                          'Name',name,...
                          'Units','pixels',...        
                          'Position',pos_clip(position),...
                          'Resize','off',...
                          'NumberTitle','off',...
                          'visible','on',...
                          'Inverthardcopy','off',...
                          'Userdata',{'to be filled in'},...
                          'Tag','siglab_mc_setup',...
                          'BackingStore','off',...
                          'CloserequestFcn','mcsetup(''close'');');
% FILL IN ALL RELEVANT UICONTROL PROPERTIES AND THEN CREATE
% OBJECTS IN ONE BIG LOOP
% uicontrol widths
  widths = [60,80,70,55,75,80,85,60,65];
  text_widths = [56,76,66,51,71,221,61];
% uicontrol positions
  right_offset = right_margin+2;         
  for i = 1:text_columns
      text_positions(i,:) = [right_offset text_top text_widths(i) text_height]; 
      right_offset = right_offset + text_widths(i) + 4;
  end;
  right_offset = right_margin;
  for i = 1:columns
      positions(i,:) = [right_offset tops(i) widths(i) heights(i)];
      right_offset = right_offset + widths(i); 
  end;
  % added auto range support for vna (RAB)
  if strcmp(owner,'vna')
     fs_list = '10.0 V|5.0 V|2.5 V|1.25 V|0.625 V|0.31 V|0.16 V|78 mV|39 mV|20 mV|Auto';
  else
     fs_list = '10.0 V|5.0 V|2.5 V|1.25 V|0.625 V|0.31 V|0.16 V|78 mV|39 mV|20 mV';
  end;

  if dsacfg('get_Bias')
     cup_list = 'AC|DC|Bias';
  else
     cup_list = 'AC|DC';
  end;
  pereu_list = 'Off|/Volt|/mV|/uV|/kV';
  styles={'check','pop','pop','edit','edit','edit','edit','pop','edit'};
  bcolors = num2cell(mc_color(...
                             [3 4 4 4 4 ...
                              4 4 4 4],:),2);
  text_strings={'On/Off','Full Scale','Coupling','Offset',...
                'Label','       Engineering Units',...
                '0 dB Vref'};
  horizontal_align={'center','center','center','center','center',...
                    'left','center'};
  empty_string = '';
  one = num2str(1);
  for i = 1:inchans
       strings(i,1:columns)={['Ch ',num2str(i)],fs_list,cup_list,...
                                num2str(ch.offsets(i)),ch.labels{i},...
                                num2str(ch.euvals(i)),ch.eus{i},....
                                pereu_list,num2str(ch.dbrefs(i))};
       % ch.eustates(i).values from vxx's perspective has only two
       % values, 1 and 0, where 1 is on and 2 is off. In mc setup, 
       % it can take on 4 states: 1, 2, 3 and 4, where 1 is off, 2 is 
       % volts, 3 is millivolts, and 4 is microvolts.
       if ch.eustates(i).values==0
           eng_on_off(i)=1;
       else
           eng_on_off(i)=2;        
       end
       values(i,1:columns)={ch.states(i),ch.fss(i),ch.cups(i),...
                            1,1,1,1,eng_on_off(i),1};



       if ch.cups(i) == 1 || ch.cups(i) == 3
          offset_enable = 'off';
       elseif ch.cups(i) == 2
          offset_enable = 'on';
       else 
          error(['illegal coupling state: ',num2str(ch.cups(i))]);
       end;
       enables(i,1:columns)={  'on','on','on',offset_enable,...
                               'on',ch.eustates(i).enables,...
                                ch.eustates(i).enables,'on',...
                               'on'};           
       callbacks(i,1:columns)={['mcsetup(''change'',''states'',',num2str(i),');'],...
                               ['mcsetup(''change'',''fss'',',num2str(i),');'],...
                               ['mcsetup(''change'',''cups'',',num2str(i),');'],...
                               ['mcsetup(''change'',''offsets'',',num2str(i),');'],...
                               ['mcsetup(''change'',''labels'',',num2str(i),');'],...
                               ['mcsetup(''change'',''euvals'',',num2str(i),');'],...
                               ['mcsetup(''change'',''eus'',',num2str(i),');'],...
                               ['mcsetup(''change'',''eustates'',',num2str(i),');'],...
                               ['mcsetup(''change'',''dbrefs'',',num2str(i),');']};
    % only text objects accept an interpreter property
    TxtInterpret(i,1:columns) = {['none','none','none','none','tex','none','tex','none','none']};
   end;
   for i = 1:text_columns
        mch(200,i) = uicontrol(  'style','text','position',text_positions(i,:),'string',text_strings{i},...
                                 'backgroundcolor',[0 1 0],'foregroundcolor',mc_color(17,:),...
                                 'horizontalalign',horizontal_align{i});
   end;
   o=100;w=60;
   uicontrol('style','text',...
                       'position',[text_positions(6,1)+o+50,text_positions(6,2),...
                                   w,text_positions(6,4)],...
                       'string','Invert',...
                       'backgroundcolor',[0 1 0],'foregroundcolor',mc_color(17,:),...
                       'horizontalalign','center');
   mch(EUTOGGLE,1)=uicontrol('style','check',...
                       'position',[text_positions(6,1)+o+w+45,text_positions(6,2),...
                                   176-(o+w),text_positions(6,4)],...
                       'backgroundcolor',[0 1 0],'foregroundcolor',mc_color(17,:),...
                       'horizontalalign','center',...
                       'callback','mcsetup(''euinvert'')');
   for i = 1:inchans
       for j = STATE:DBREF
           mch(j,i)=uicontrol('style',styles{j},...
                              'position',positions(j,:)-[0 i*(heights(1)+vertical_spacing) 0 0],...
                              'string',strings{i,j},...
                              'backgroundcolor',bcolors{j},...
                              'foregroundcolor',mc_color(17,:),...
                              'value',values{i,j},...
                              'enable',enables{i,j},...
                              'callback',callbacks{i,j});      
       end;
   end;
   % Channels 1 is always ON for vna
   if strcmp(owner,'vna')
       set(mch(STATE,1),'enable','off');
   end;
   for i = 1:inchans
       checkval(mch(OFFSET,i),-8,8,ch.offsets(i),'%8.8g');
       checkval(mch(EUVAL,i),-Inf,Inf,ch.euvals(i),'%8.8g');
       checkval(mch(DBREF,i),-Inf,Inf,ch.dbrefs(i),'%8.8g');
   end;
   ch.euscale = 1;
   % Create objects beneath the channel array for Apply, Close, Set All, and
   % calling up MCview.
   % vna controls channel on/off status in version 2.25 and greater s/w
   % Back to using mcsetup for ch on/off control, 5/19/98
   refchan=2;
   mch(SETALL,1)=uicontrol('style','check','string',...
           ['Set Ch ',num2str(refchan+1),' thru Ch ',num2str(In1),' = '],...
           'position',[5 6 140 24],...
           'value',0,...
           'enable','on',...
           'callback','mcsetup(''change'',''states'',1);');
   mch(REFCHAN,1)=uicontrol('style','pop','string','Ch 1|Ch 2',...
           'position',[145 2 60 28],'value',refchan,...
           'userdata',refchan,...
           'callback','mcsetup(''changeref'')'); 
   uicontrol('style','push','string','Multi-Channel PreView',...
           'position',[210 5 140 25],...
           'enable','on',...
           'visible','off',...
           'callback','mcsetup(''mcview'');');
   mch(APPLY,1)=uicontrol('style','push','string','Apply',...
           'foregroundcolor',[1 0 0],...
           'fontsize',8,...
           'fontweight','normal',...
           'position',[355 5 50 25],...
           'enable','off',...
           'callback','mcsetup(''apply'');');
   mch(UNDO,1)=uicontrol('style','push','string','Undo',...
           'position',[410 5 50 25],...
           'enable','off',...
           'callback','mcsetup(''undo'');');
   mch(SAVEAS,1)=uicontrol('style','push','string','Save As',...
           'position',[465 5 50 25],...
           'callback','mcsetup(''save as'');');
   uicontrol('style','push','string','Close',...
           'position',[520 5 50 25],...
           'callback','mcsetup(''close'');');
   % save necessary data in main figure userdata
   set(mch(FIGURE,1),'userdata',{inchans,mch,ch});
   % reset the uifont as it was "in the beginning"
   uifont(SaveFont);
case 'changeref'
   oldrefchan=get(mch(REFCHAN,1),'userdata');
   newrefchan=get(mch(REFCHAN,1),'value');
   % only take action if selection is different from existing one
   if newrefchan~=oldrefchan
       set(mch(SETALL,1),'string',...
       ['Set Ch ',num2str(newrefchan+1),' thru Ch ',num2str(inchans),' = ']);      
       mcsetup('change','states',newrefchan); 
       set(mch(REFCHAN,1),'userdata',newrefchan);      
   end;
case 'mcview'
   mcview('init');  
case 'undo'
   set(mch(UNDO,1),'enable','off');
   mcsetup('fromvxx','all');
case 'save as'
   % Check to see if plot_vxx is running. If running
   % disallow turning channels on and off
   vdlg1_owner = get(HVDLG1_(16),'userdata');
   eval(['rs=plot_',vdlg1_owner,'(''get'',''rs'');']);
   if rs==1
       msgbox('You cannot save while acquiring, hit the Stop button to terminate acquisition first.',...
       'SigLab Warning',...
       'warn',...
       'modal');
       set(mch(STATE,In2),'value',~get(mch(STATE,In2),'value'));
       return;
   else
       eval([vdlg1_owner,'(''save_as'');']);
   end;
case 'euinvert'
   for i = 1:inchans
       pereupos=get(mch(PEREU,i),'position');
       eupos=get(mch(EU,i),'position');
       set(mch(EU,i),'position',pereupos);
       set(mch(PEREU,i),'position',eupos);
       eus=get(mch(EU,i),'string');
       index = get(mch(PEREU,i),'value');
       if index == 1
           ch.euscale = 1;
       elseif index == 2
           ch.euscale = 1;
       elseif index == 3
           ch.euscale = 0.001;
       elseif index == 4
           ch.euscale = 1e-06;
       elseif index == 5
           ch.euscale = 1000;
       end;
       if get(mch(EUTOGGLE,1),'value')
           set(mch(PEREU,i),'string','Off|Volt|mV|uV|/kV');
           if eus(1)~='/'
               set(mch(EU,i),'string',['/',eus]);
           end;
           set(mch(EUVAL,i),'string',num2str(1/(ch.euscale*ch.euvals(i))));
       else
           set(mch(PEREU,i),'string','Off|/Volt|/mV|/uV|/kV'); 
           if eus(1)=='/'
               set(mch(EU,i),'string',eus(2:length(eus)));
           end;  
           set(mch(EUVAL,i),'string',num2str(ch.euvals(i)*ch.euscale));
       end;
   end;
% As the name implies, when someone changes a setting in the
% mc gui, this callback is activated.
% The offsets, euvals, and dbrefs must be validated
% everytime the user changes them. If these entries
% are invalid, they are automatically changed back
% to their previous "valid" value.
case 'change'
  set_all = get(mch(SETALL,1),'value');
  set_all_refchan = get(mch(REFCHAN,1),'value');
  switch In1
    case 'states'
      % Check to see if plot_vxx is running. If running
      % disallow turning channels on and off and just return.
      vdlg1_owner = get(HVDLG1_(16),'userdata');
      eval(['rs=plot_',vdlg1_owner,'(''get'',''rs'');']);
      % Address the run/stop concerns of the parent.
      if rs
        set(mch(FIGURE,1),'name','MC Setup: Cannot enable/disable channels while acquiring!');
        % Change the channel on/off status back to the way it was before the
        % user clicked on it. This is to undo the user's check. Only do
        % this on channel 2 and higher since channel 1 should never be
        % turned off.
        if In2~=1
          set(mch(STATE,In2),'value',~get(mch(STATE,In2),'value'));
        end
        return;
      else
        set(mch(FIGURE,1),'name','MC Setup');
      end
      % Change all the channels if the set all box is checked and 
      % the magic In2 parameter equals 1. In2 = 1 only occurs when
      % the Set All check box is clicked on.
      if set_all && In2==1
        for i = set_all_refchan+1:inchans
            ch.states(i) = ch.states(set_all_refchan);
            set(mch(STATE,i),'value',ch.states(set_all_refchan));
        end
      else
        ch.states(In2) = get(mch(STATE,In2),'value');  
      end

      % At least one channel must be on at all times
      % In this case, if an attempt is made to turn
      % all channels off at once, Ch 1 will remain on.
      if sum(ch.states)==0
        ch.states(1)=1;
        set(mch(STATE,1),'value',1);
      end
    case 'fss'
         ch.fss(In2)=get(mch(FS,In2),'value');
         if ch.fss(In2) > 2
            mcsetup('change','offsets',In2);
         end
         if set_all && (In2==set_all_refchan)
            for i = set_all_refchan+1:inchans
                ch.fss(i) = ch.fss(set_all_refchan);
                set(mch(FS,i),'value',ch.fss(set_all_refchan));
            end
         end
   case 'cups'
         ch.cups(In2) = get(mch(CUP,In2),'value');
         if ch.cups(In2)==1 || ch.cups(In2)==3
            set(mch(OFFSET,In2),'enable','off');
         elseif ch.cups(In2)==2
            set(mch(OFFSET,In2),'enable','on');
         else
            error(['illegal state for coupling control: ',num2str(ch.cups(In2))]);
         end

         if set_all && (In2==set_all_refchan)
            for i = set_all_refchan+1:inchans
                ch.cups(i) = ch.cups(set_all_refchan);
                if ch.cups(i)==1 || ch.cups(In2)==3
                   set(mch(OFFSET,i),'enable','off');
                elseif ch.cups(i)==2
                   set(mch(OFFSET,i),'enable','on');
                else
                   error(['illegal state for coupling control: ',num2str(ch.cups(i))]);
                end 
                set(mch(CUP,i),'value',ch.cups(set_all_refchan));
            end
         end
  case 'offsets'
       fs_index = get(mch(FS,In2),'value');
       if fs_index > 2
          maxv = 2.5; minv = -2.5;
       else
          maxv = 8; minv = -8;
       end
       ch.offsets(In2) = checkval(mch(OFFSET,In2),...
       minv,maxv,ch.offsets(In2),'%8.8g');
       if set_all && (In2==set_all_refchan)
          for i = set_all_refchan+1:inchans
              ch.offsets(i) = ch.offsets(set_all_refchan);
              set(mch(OFFSET,i),'string',num2str(ch.offsets(set_all_refchan)));
              ch.offsets(i) = checkval(mch(OFFSET,i),...
                              -Inf,Inf,ch.offsets(set_all_refchan),'%8.8g');
          end
       end
  case 'labels'
       ch.labels{In2} = get(mch(LABEL,In2),'string'); 
       if set_all && (In2==set_all_refchan) 
          location=findstr(ch.labels{set_all_refchan},num2str(set_all_refchan));
          temp_str = ch.labels{set_all_refchan};
          for i = 1:inchans
              ch.labels{i} = [temp_str(1:location-1),num2str(i),temp_str(location+1:length(temp_str))];
              set(mch(LABEL,i),'string',[ch.labels{i}]);
          end
       end
  case 'euvals'
        if get(mch(EUTOGGLE,1),'value')==1
            new_euval = s2n(get(gcbo,'string'));
            if isempty(new_euval) || isinf(1/new_euval)
                set(gcbo,'string',ftoa('%7.3g',1/ch.euvals(In2)));
                return
            end
           ch.euvals(In2)=1/(ch.euscale*checkval(gcbo,-Inf,Inf,ch.euvals(In2),'%7.3g')); 
        elseif get(mch(EUTOGGLE,1),'value')==0
           ch.euvals(In2)=(1/ch.euscale)*checkval(gcbo,-Inf,Inf,ch.euvals(In2),'%7.3g');
        else
           error('Illegal value for EU toggle state');
        end
        if set_all && (In2==set_all_refchan)
           euval_string = get(mch(EUVAL,set_all_refchan),'string');
           for i = set_all_refchan+1:inchans
               ch.euvals(i) = ch.euvals(set_all_refchan);
               set(mch(EUVAL,i),'string',euval_string);
           end
        end
  case 'eus'
       if get(mch(EUTOGGLE,1),'value')
          eu_string = get(mch(EU,In2),'string');
          % must remove "/" character
          ch.eus{In2} = eu_string(2:length(eu_string));
       else
          ch.eus{In2} = get(mch(EU,In2),'string');
       end
       if set_all && (In2==set_all_refchan)
          for i = set_all_refchan+1:inchans
              ch.eus{i} = ch.eus{set_all_refchan};
              set(mch(EU,i),'string',get(mch(EU,set_all_refchan),'string'));
          end
       end
  case 'eustates'
      % ch.eustates(In2).strings is always '/Volt' as
      % VXX software doesn't understand anything else
      eustate = get(mch(PEREU,In2),'value');
      if eustate == 1
          % for no EUs
          ch.eustates(In2).enables = 'off';
          ch.eustates(In2).strings = 'Off';
          ch.eustates(In2).values = 0;
          set(mch(EU,In2),'enable','off');
          set(mch(EUVAL,In2),'enable','off');
      elseif eustate == 2
          % for /volt EUs
          ch.euscale = 1;
          ch.eustates(In2).enables = 'on';
          ch.eustates(In2).strings = '/Volt';
          ch.eustates(In2).values = 1;
          set(mch(EU,In2),'enable','on');
          set(mch(EUVAL,In2),'enable','on');
          if get(mch(EUTOGGLE,1),'value')
             set(mch(EUVAL,In2),'string',num2str(1/(ch.euscale*ch.euvals(In2)))); 
          else  
             set(mch(EUVAL,In2),'string',num2str(ch.euvals(In2)*ch.euscale));  
          end
     elseif eustate == 3
          % for /milli-volt EUs
          ch.euscale = 0.001;
          ch.eustates(In2).enables = 'on';
          ch.eustates(In2).strings = '/Volt';
          ch.eustates(In2).values = 1;
          set(mch(EU,In2),'enable','on');
          set(mch(EUVAL,In2),'enable','on');
          if get(mch(EUTOGGLE,1),'value')
             set(mch(EUVAL,In2),'string',num2str(1/(ch.euscale*ch.euvals(In2))));
          else  
             set(mch(EUVAL,In2),'string',num2str(ch.euvals(In2)*ch.euscale));  
          end
      elseif eustate == 4
          % for /micro-volt EUs
          ch.euscale = 1e-06;
          ch.eustates(In2).enables = 'on';
          ch.eustates(In2).strings = '/Volt';
          ch.eustates(In2).values = 1;
          set(mch(EU,In2),'enable','on');
          set(mch(EUVAL,In2),'enable','on');
          if get(mch(EUTOGGLE,1),'value')
             set(mch(EUVAL,In2),'string',num2str(1/(ch.euscale*ch.euvals(In2)))); 
          else  
             set(mch(EUVAL,In2),'string',num2str(ch.euvals(In2)*ch.euscale));  
          end
      elseif eustate == 5
          % for /kilo-volt EUs
          ch.euscale = 1000;
          ch.eustates(In2).enables = 'on';
          ch.eustates(In2).strings = '/Volt';
          ch.eustates(In2).values = 1;
          set(mch(EU,In2),'enable','on');
          set(mch(EUVAL,In2),'enable','on');
          if get(mch(EUTOGGLE,1),'value')
             set(mch(EUVAL,In2),'string',num2str(1/(ch.euscale*ch.euvals(In2)))); 
          else  
             set(mch(EUVAL,In2),'string',num2str(ch.euvals(In2)*ch.euscale));  
          end
      else
          error(['eustate ''',num2str(eustate),''' undefined']);
      end

    % the 'nargin==3' code is a fix for an infinite recursion, 6/25/98
    if set_all && (In2==set_all_refchan) && (nargin==3)
       for i = set_all_refchan+1:inchans
           ch.eustates(i).values = ch.eustates(set_all_refchan).values;
           set(mch(PEREU,i),'value',eustate);
           set(mch(EUVAL,i),'enable',ch.eustates(set_all_refchan).enables);
           set(mch(EU,i),'enable',ch.eustates(set_all_refchan).enables);
           mcsetup('change','eustates',i,'non-recursive');
       end
    end
  case 'dbrefs'
       ch.dbrefs(In2) = checkval(mch(DBREF,In2),...
                               -Inf,Inf,ch.dbrefs(In2),'%f');
       if set_all && (In2==set_all_refchan)
          for i = set_all_refchan+1:inchans
              ch.dbrefs(i) = ch.dbrefs(set_all_refchan);
              set(mch(DBREF,i),'string',num2str(ch.dbrefs(set_all_refchan)));
              ch.dbrefs(i) = checkval(mch(DBREF,i),...
                                      -Inf,Inf,ch.dbrefs(set_all_refchan),'%f');
          end
       end
   otherwise
       error(['Undefined behavior in Action:  ''',Action,''''])
   end

set(mch(APPLY,1),'enable','on',...
                  'fontsize',10,...
                 'fontweight','bold');
set(mch(UNDO,1),'enable','on');
% modal doesn't work in version 5.1
% set(mch(FIGURE,1),'windowstyle','modal'); drawnow;
gcf_ud{3} = ch;
set(mch(FIGURE,1),'userdata',gcf_ud);
% The APPLY function is a separate button click in the 
% user interface. APPLY sets the VLDGeez and then calls the 
% respective vxx to update that gui.
case 'apply'
  % Check to see if plot_vxx is running. If running
  % disallow turning channels on and off
  vdlg1_owner = get(HVDLG1_(16),'userdata');
  eval(['rs=plot_',vdlg1_owner,'(''get'',''rs'');']);
  if rs==1
      msgbox('You cannot apply while acquiring, hit the Stop button to terminate acquisition first.',...
          'SigLab Warning',...
          'warn',...
          'modal');
  else
      range = 1:inchans;
      VDLG1_S1(range,2)     = ch.states(range)';
      VDLG1_S1(range,1)     = ch.fss(range)';
      VDLG1_S1(range,3)     = ch.cups(range)'-1;
      VDLG1_S1(range,4)     = ch.dbrefs(range)';
      VDLG1_S1(range,6)     = ch.offsets(range)';
      VDLG1_S1(range,7)     = ch.euvals(range)';
      for i = 1:inchans
          VDLG1_S1(i,5) = ch.eustates(i).values;
      end
      for i = 1:inchans
          VDLG1_S2(i,:) = strpack(19,ch.labels{i},ch.eus{i});
      end
      set(mch(FIGURE,1),'userdata',gcf_ud);
      v_dlg1('load_mc');
      % User should know when settings in the mc setup have changed
      % If nothing has changed, then hitting Apply is redundant.
      set(mch(APPLY,1),'enable','off',...
          'fontsize',8,...
          'fontweight','normal');
      set(mch(UNDO,1),'enable','off');
  end
   % modal doesn't work in version 5.1
   % set(mch(FIGURE,1),'windowstyle','normal');
% FROMVXX is called by vxx when a channel setup
% parameter is changed in vxx
case 'fromvxx'
    % The name here is still old as it does not
    % get updated until the return from mc to vxx.
    %name = get(gcbf,'name');	
    %set(mch(FIGURE,1),'name',['Channel Setup: ',name]);	
    states = VDLG1_S1(:,2);
    fss    = VDLG1_S1(:,1);
    cups   = VDLG1_S1(:,3);
    dbrefs = VDLG1_S1(:,4);
    eustate= VDLG1_S1(:,5);
    offsets= VDLG1_S1(:,6);
    euvals = VDLG1_S1(:,7);

    if strcmp(In1,'all')
       start = 1; stop = inchans;
    elseif In1<=inchans
       start = In1; stop = In1;
    else
       error(['Invalid value for inchans =',inchans]);
    end

   % Both the ch struct value and the mc gui have to be changed
   % to reflect the change in vxx.
   for i = start:stop
       if eustate(i) == 1
          ch.eustates(i).strings = '/Volt';
          ch.eustates(i).enables = 'on';
          ch.eustates(i).values = 1;
          set(mch(EU,i),'enable','on');
          set(mch(EUVAL,i),'enable','on');
          set(mch(PEREU,i),'value',2);
       else
          ch.eustates(i).strings = 'Off';
          ch.eustates(i).enables = 'off';
          ch.eustates(i).values = 0;
          set(mch(EU,i),'enable','off');
          set(mch(EUVAL,i),'enable','off');
          set(mch(PEREU,i),'value',1);	
       end
       ch.states(i) = states(i);
       set(mch(STATE,i),'value',states(i));	
       ch.fss(i) = fss(i);
       set(mch(FS,i),'value',fss(i));	
       ch.cups(i) = cups(i)+1;
       set(mch(CUP,i),'value',cups(i)+1);	
       ch.offsets(i) = offsets(i);
       fs_index = get(mch(FS,i),'value');
       if fs_index > 2
          maxv = 2.5; minv = -2.5;
       else
          maxv = 8; minv = -8;
       end
       set(mch(OFFSET,i),'string',num2str(ch.offsets(i)));
       ch.offsets(i) = checkval(mch(OFFSET,i),...
                                minv,maxv,ch.offsets(i),'%8.8g');
       % enable or disable dc offset depending on ac/dc coupling
       if cups(i)
           set(mch(OFFSET,i),'enable','on');
       else
           set(mch(OFFSET,i),'enable','off');
       end
       ch.labels{i} = pullstr(VDLG1_S2(i,:),1);
       set(mch(LABEL,i),'string',ch.labels{i});	
       ch.euvals(i) = euvals(i);
       set(mch(EUVAL,i),'string',num2str(ch.euvals(i)));
       ch.euvals(i) = checkval(mch(EUVAL,i),...
                                -Inf,Inf,ch.euvals(i),'%8.8g');
       ch.eus{i} = pullstr(VDLG1_S2(i,:),2);
       eus=ch.eus{i};
       if get(mch(EUTOGGLE,1),'value')
           eus=['/',ch.eus{i}];
       end
       set(mch(EU,i),'string',eus);
       ch.dbrefs(i) = dbrefs(i);
       set(mch(DBREF,i),'string',num2str(ch.dbrefs(i)));
       ch.dbrefs(i) = checkval(mch(DBREF,i),...
                               -Inf,Inf,ch.dbrefs(i),'%8.8g');
       gcf_ud{3} = ch;
       set(mch(FIGURE,1),'userdata',gcf_ud);
   end
   h=findobj('tag','siglab_mc_view');
   if ~isempty(h)
       mcview('XYupdate');    
   end
% case 'fromvxx_clist' was added 5/19/98 to satisfy the requirement of
% users turning on/off CrossChans in vna and this in turn has the power
% to turn certain channels on and off affecting the mcsetup readout.
case 'fromvxx_clist'
   for i=1:inchans
       if ismember(i,In1)
          ch.states(i) = 1;
          set(mch(STATE,i),'value',1);  
       else
          ch.states(i) = 0;
          set(mch(STATE,i),'value',0);  
       end
   end
   gcf_ud{3} = ch;
   set(mch(FIGURE,1),'userdata',gcf_ud);      
case 'close'
   mcsetpos = get(mch(FIGURE,1),'position');
   mcsetinchans = inchans;
   mcsetpos = {mcsetpos,mcsetinchans};
   delete(mch(FIGURE,1));
   [drv, pth]=pathfind('vcom');
   filename = [drv,pth,'\mcsetpos.mat'];
   try
       save(filename,'mcsetpos');
   catch
       error('Could not save position.');
   end
%    eval(['save ',drv,pth,'\mcsetpos mcsetpos'],['error([''Could not save position''])']); 
otherwise  % end 'Action' switch
	error(['Action:  ''',Action,''' undefined']);   
end
% end function mcsetup()





