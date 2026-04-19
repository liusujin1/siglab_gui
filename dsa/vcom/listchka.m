  function Out1 = listchka(hin,Action,In1,In2,In3)
% function Out1 = listchka(hin,Action,In1,In2,In3)
% For all but the 'init' Action, hin is the handle to this pseudo object
% Action 
%  'init' 
%      hin              parent handle
%      In1.position     [x y w h] in pixels , sets position of the lower left corner of label, and label h
%      In1.visible      'on' or 'off'
%      In1.maxnum       max number of check boxes
%      In1.maxrow       maximum number of rows
%      In1.label        text label 
%      In1.cb_close     callback on close iff there was a change in state
%      In1.cb_check     callback for change in check box state
%      In1.clabel       color of label
%      In1.cframe       color of frame
%      In1.fontname     fontname
%      In1.fontsize     size in points
%      In1.fontweight   light | {normal} | demi | bold
%
%      In2().label      array of strings  e.g. In1.labels{i} is the ith string 
%      In2().color      array of text colors to match channel line colors
%      In2().state      state of checkboxes
%      In2().chanpair   channel or channel pair that corresponds to this entry
%
%      Out1             handle of this pseudo object (it will be an integer)
%
% 'set'                
%      'state'          defines how many check boxes are gonna be active
%          In2().label      array of strings  e.g. In1.labels{i} is the ith string 
%          In2().color      array of text colors to match channels
%          In2().state      state of checkboxes
%      
%      'visible'
%          In2          'on' or 'off'
%      'position'
%          In2          [x y]  repositions this pseudo object
%      'disable'
%                       sets channels In In2 to a greyed (disabled) state ... if [] is passed, nobody is greyed. 
%
% 'get'
%      'state'          string passed to In1
%      Out1             full  array of check box states
%                       Out1().state
%                       Out1().label
%                       Out1().color
%                     
%      'change'
%      Out1             Out1.ckbox  which checkbox changed
%                       Out1.value  its current state
%      'active'         Out1 has vector of channels 
%              
% 'cb_check'           
%      cknum            checkbox number , internally used
%  
% 'cb_button'           when  button is pushed  toggles vivibility state of frame and
%                       active check boxes and executes the user callback loaded during the 'init'
% 'open'                shows check boxes if obj is "vivible" 

% Dick Benson, DSP Technology

  % Use the findobj technique (a'la slider),
  % in liu of maintaining a global static array of handles. 
   if ~strcmp(Action,'init')
       h = findobj('tag','ListCheck','value',hin);
       if isempty(h)
          error('failure to locate ListCheck internal handle');
       else
          hlh = get(h,'userdata');   % get local handles
       end;
   end; 
  
   switch Action
      case 'init'
         % pick a handle ... but not just any handle ... 
         usedhandles = get(findobj('Tag','ListCheck'),'value');
         if isempty(usedhandles)
            Out1 = 1;  % Out1 will be the handle of this pseudo object
         else 
            if iscell(usedhandles)
               usedhandles =[usedhandles{:}]; % need to convert for setdiff to fly
            end; 
            avail = setdiff(1:(max(usedhandles)+1),usedhandles); % check for holes in used handles
            Out1  = avail(1);           % pick 1st available slot
         end;   
        
         x     = In1.position(1);
         y     = In1.position(2);
         w     = In1.position(3);
         h     = In1.position(4);
         wpb   = h;
         ratio = wpb/w;
        
         hlh.numactive = length(In2);
        
      
         hlh.frame = uicontrol(hin,'Style','frame','visible','off',...
                                   'Position',[x-1,y-(min(hlh.numactive+1,In1.maxrow+1))*h-1,w+2,(min(hlh.numactive+1,In1.maxrow+1))*h+1],...
                                   'Tag','ListCheck','Value',Out1,...
                                   'BackGroundColor',In1.cframe,...
                                   'userdata',[]);
                                    % The userdata will hold structure containing the handles of the 
                                    % "real" objects that make up this this pseudo object,
                                    % the value property contains the pseudo object (pseudo!) handle. 
                                  
         hlh.label = uicontrol(hin,'Style','text','visible',In1.visible,...
                                   'Position',In1.position.*[1 1 (1-ratio) 1],...
                                   'String',In1.label ,...
                                   'FontName',In1.fontname,...
                                   'FontSize',In1.fontsize,...
                                   'FontWeight',In1.fontweight,...
                                   'BackGroundColor',In1.clabel,...
                                   'ForeGroundColor',[0 0 0],...
                                   'HorizontalAlignment','center',...
                                   'enable','inactive',...
                                   'ButtonDownFcn',sprintf('listchka(%d,''cb_button'')',Out1),...
                                   'userdata',In1.cb_check);
                                   % note button down fcn wont work unless enable is inactive (sure!)
                                  
        hlh.button = uicontrol(hin,'Style','Pushbutton','visible',In1.visible,...
                                   'Position',[x+(1-ratio)*w,y,wpb,h],...
                                   'BackGroundColor',0.7529*[1,1,1],...
                                   'String','V' ,...
                                   'FontName',In1.fontname,...
                                   'FontSize',In1.fontsize,...
                                   'FontWeight',In1.fontweight,...
                                   'HorizontalAlignment','center',...
                                   'userdata',In1.cb_close,...
                                   'callback',sprintf('listchka(%d,''cb_button'')',Out1));

        

        nrow = In1.maxrow;
        wp   = w/(fix((In1.maxnum-1)/nrow)+1); % divide field width by # of columns, computed from nrow
        for i=1:In1.maxnum
            if i <= hlh.numactive
               s   = In2(i).label;
               c   = In2(i).color;
               v   = In2(i).state;
            else
               s   = '';
               c   = [0.5 0.5 0.5];
               v   = 0;
            end;
            xp      = x+(fix((i-1)/nrow))*wp;
            yp      = y - (rem(i-1,nrow)+1)*h;
        
            hlh.hckb(i) = uicontrol(hin,'Style','checkbox','visible','off',...
                                        'Position',[xp,yp,wp,h],...
                                        'BackGroundColor',In1.cframe,...
                                        'String',s,...
                                        'value',v,...
                                        'ForeGroundColor',c,...
                                        'FontName',In1.fontname,...
                                        'FontSize',In1.fontsize,...
                                        'FontWeight',In1.fontweight,...
                                        'HorizontalAlignment','left',...
                                        'Interruptible','off',...
                                        'userdata',[],...
                                        'callback',sprintf('listchka(%d,''cb_check'',%d)',Out1,i));
           % Note interruptible 'off' is required to keep fast mouse clickers from 
           % interrupting screen repaints. If it is 'on', the host gui will get confused. 
           % userdata  occupied with channel number 
        end;
        
        % add some ancillary variables to the structure
        hlh.change    = 1;
        hlh.y0        = y;  % y coord of this pseudo object
        hlh.x0        = x;  % x   "  
        hlh.hofck     = h;
        hlh.maxrow    = In1.maxrow;
        hlh.maxnum    = In1.maxnum;
        hlh.chbchg    = 0;   % check box number that changed state
        hlh.open      = 0;   % is box "open" or closed. 
        hlh.refcindex = [];   % index to object that is ref channel
        set(hlh.frame,'userdata',hlh);                            

      case 'cb_button'
           if strcmp(get(hlh.frame,'visible'),'on') 
              vis       = 'off';
              hlh.open  = 0; 
           else 
              vis       = 'on';
              hlh.open  = 1;
           end;
           set(hlh.frame,'visible',vis);
           set([hlh.hckb(1:hlh.numactive)],'visible',vis);
           if hlh.change ==1
              hlh.change = 0;
              eval(get(hlh.button,'userdata'));  % implement user defined callback
           end;
           set(hlh.frame,'userdata',hlh);
          
           % the following has other side effects like rearranging the menu and 
           % significant screen spasms .... must abandon
           %if strcmp(vis,'on')
           %   listchka(hin,'on_top');   % hocus-pocus
           %end;
           
      case 'open'
           if strcmp(get(hlh.button,'visible'),'on')
              set(hlh.frame,'visible','on');
              set([hlh.hckb(1:hlh.numactive)],'visible','on');
              hlh.open  = 1;
           end;

      case 'cb_check'
           hlh.change = 1;                   % set the change flag
           hlh.chbchg = In1;                 % log which checkbox changed state
           set(hlh.frame,'userdata',hlh);    % save 
           eval(get(hlh.label,'userdata'));  % implement user defined callback
     
      case 'set'
           switch In1
           case 'state'
               hlh.numactive = length(In2);
               if strcmp(get(hlh.frame,'visible'),'on') vis = 'on'; else vis = 'off'; end;
               for i=1:hlh.numactive
                   set(hlh.hckb(i),'string',In2(i).label,...
                                   'foregroundcolor',In2(i).color,...
                                   'value',In2(i).state,...
                                   'userdata',In2(i).respchan,...
                                   'visible',vis);
               end;
               
               if hlh.numactive < length(hlh.hckb)
                  set(hlh.hckb((hlh.numactive+1):length(hlh.hckb)),'visible','off');
               end;
           
               pos        = get(hlh.frame,'position');
               pos(2)     = hlh.y0 -min(hlh.numactive,hlh.maxrow)*hlh.hofck-1;
               pos(4)     = min(hlh.numactive,hlh.maxrow)*hlh.hofck+2;
               set(hlh.frame,'position',pos,'userdata',hlh);
           
           case 'disable'
               % sets channel to a greyed state ... if [] is passed, 
               % nobody is greyed. 
               % In2 has list of channels
               for i=1:hlh.numactive
                   if ismember(get(hlh.hckb(i),'userdata'),In2)
                       set(hlh.hckb(i),'enable','off');
                   else
                       set(hlh.hckb(i),'enable','on');
                   end;
               end;
               
           case 'visible'
               if strcmp(In2,'on')
                    if hlh.open
                       set([hlh.hckb(1:hlh.numactive),hlh.frame,hlh.button,hlh.label],'visible','on');
                    else
                       set([hlh.button,hlh.label],'visible','on');
                    end;
               else
                  set([hlh.button,hlh.frame,hlh.label,hlh.hckb(1:hlh.numactive)],'visible','off');
               end;

           
           case 'position'
                x1 = In2(1); 
                y1 = In2(2);
                % must move each object
                for handle = [hlh.frame,hlh.label,hlh.button,hlh.hckb(1:hlh.maxnum)]
                    pos = get(handle,'position');
                     set(handle,'position',[x1+pos(1)-hlh.x0, y1+pos(2)-hlh.y0,pos(3),pos(4)]);
                end;
                hlh.y0        = y1;
                hlh.x0        = x1;
                set(hlh.frame,'userdata',hlh); 
           otherwise
              disp(['error in listcka set action:',In1])
           end; % switch In2 


      case 'on_top'
           % 
           hp      = get(hlh.frame,'parent');                      % handle of parent
           handles = get(hp,'children');                           % children of parent
           myobjs  = [hlh.frame,hlh.hckb(1:hlh.numactive)]';       % these gui objects
           set(hp,'children',[myobjs ; setdiff(handles,myobjs)]);  % make them first on list
           % disp('on_top')

      case 'get'
          switch In1
             case 'state'
                  c.label =  '';
                  c.color =  [0 0 0];
                  c.state =  0;
                  Out1(1:hlh.numactive)=c;
          
                  for i=1:hlh.numactive
                      Out1(i).state     = get(hlh.hckb(i),'value');
                      Out1(i).label     = get(hlh.hckb(i),'string');
                      Out1(i).color     = get(hlh.hckb(i),'foregroundcolor');
                  end; 
                  
             case 'change'
                   Out1.ckbox = hlh.chbchg;
                   Out1.value = get(hlh.hckb(hlh.chbchg),'value');
                   
             case 'active'
                   Out1 = [];
                   for i=1:hlh.numactive
                      if get(hlh.hckb(i),'value')
                         Out1=[Out1, get(hlh.hckb(i),'userdata')]; % note comma to make a row vector
                      end;
                   end;
             otherwise
             disp(['invalid sub action in listchk1 get: ',In1]);
          end;
          
      otherwise 
           disp(['unrecognized action:',Action,' in listchka.m']);
    
   end;  % main Action switchyard construction 
% end function listchka














