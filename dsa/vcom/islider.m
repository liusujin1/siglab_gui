  function Out1=islider(Hin,Action,In1,In2,In3,In4,In5,In6,In7,In8,In9,In10)
% function Out1=islider(Hin,Action,In1,In2,In3,In4,In5,In6,In7,In8,In9,In10)   
% Integrated slider support.
% Creates a pseudo object that has the 5 following sub objects:
%  ------label-----
%  min---value--max
%  -----slider-----
%
% Hin is the main handle submitted to the this function that was returned by the init action 
% Action
%     'init'      In1= [x,y,w] in pixels
%                 In2= [sldmin,sldmax,value,edmin,edmax] values
%                 In3= slider label, no label if In3=''
%                 In4= slider call back   
%                 In5= not used (for compatibility with older rev)
%                 In6= 'on' or 'off' (visibility)
%                 In7= mode 1=linear
%                           2=linear integers
%                           3=power of 2
%                           4=linear quantized to In7(2)
%                           5= quasi log with min step size of In7(2)
%                 In8= [[r g b];   label background color
%                       [r g b];   edit field background color
%                       [r g b];   label foreground color, [0 0 0] if not given
%                       [r g b]]   edit field foreground color, default=[0 0 0] 
%                 In9= [fmtmin fmtval fmtmax] format strings
%                      e.g['%2.1f';'%6.1f';'%5.0f']
%                 In10 = [optional], Parent figure handle (hf) added to
%                                    uicontrols, uimenus, and islider to keep objects 
%                                    in parent figure with monkey on mouse. 
%                 Out1 returns main handle of this pseudo object
%     'get'       Out1 returns the current value of the integrated slider
%     'set'
%       'minmax'    In2 [sldmin,sldmax,edmin,edmax] 
%                   In3 (optional) can have a new label
%                   In4 (optional) visibility
%                   In5 (optional) value
%                   Out1= returns a possibly limited value, do not ignore! 
%       'value'     In2 has target slider value, Out1 returns a possibly limited value, do not ignore! 
%       'vis_on'    optional 'no_label' in arg In2
%       'vis_off'
%       'ena_on'    enable
%       'ena_off'   disable
%       'label'     In2 set label string
%                   In3 (optional) visibility
%       'newquant'  In2 has a new quantization value
%       'position'  In2= [x,y,w] in pixels

% Dick Benson, DSP Technology

%include
% vsiz_h.m
% Header file with slider mode definitions for islider.mi, sldclickm, and users
%end_include
  
%define
  %label    =1;
  %slide    =2;
  %mintxt   =3;
  %maxtxt   =4;
  %valro    =5;
  %lastobj  =5;
  
  %mode1    =6;
  %quant    =7;
  %last_h   =7;
  
  %min_ed   =1;
  %max_ed   =2;
%end_define

   if strcmp(Action,'init')~=1
      h = findobj('Tag','PseudoSlider','Value',Hin);
      if length(h) ~= 1 
          disp('Bad PseudoSlider handle');
          return;
      end
      h = get(h,'userdata');             % get local handles
      fmt= get(h(3),'userdata');    % get formating info
   end

   if strcmp(Action,'init')   % create and position slider objects
      h       = zeros(1,7);
      fmt     = In9;
      
      if nargin <=11
         hf = gcf;
      else
         hf = In10;
      end;
      
      min_str = ftoa(fmt(1,:),In2(1));
      max_str = ftoa(fmt(3,:),In2(2)); 

      if length(In8(:,1))<=2 
          In8=[In8;0 0 0;0 0 0]; 
      end;
      if strcmp(In3,'') 
          label_vis='off';
      else
          label_vis=In6;
      end

      slist = findobj('Tag','PseudoSlider'); % get list of slider text handles
      Out1 = 0;         % Out1 will be the handle of the pseudo object
      % for k=slist' 
      for k=slist'
          Out1 = max(Out1,get(k,'Value'));
      end;
      Out1 = Out1 + 1;  % slider object handle is 1 bigger than all others.
      h(1) =uicontrol(hf,'Style','text','visible',label_vis,...
                        'Tag','PseudoSlider','Value',Out1,...
                        'String',In3,...
                        'BackGroundColor',In8(1,:),...
                        'ForeGroundColor',In8(3,:),...
                        'HorizontalAlignment','center');
       % userdata in this label is a vector of all the handles of the
       % pseudo slider. The Value property contains the pseudo slider handle.
                                  
      h(2) =   uicontrol(hf,'Style','slider','visible',In6,... 
                        'BackGroundColor',In8(1,:),...
                        'Min',In2(1),'Max',In2(2),'Value',max(min(In2(3),In2(2)),In2(1)),...
                        'userdata',In2(3),...
                        'Callback',sprintf('islider(%d,''CBslide'')',Out1));    
      % Compatibility tweak: Stop-at-count slider should step by 5.
      if ischar(In4) && ~isempty(strfind(In4,'set_tcntro'))
          rng = In2(2) - In2(1);
          if rng > 0
              stp = min(max(5/rng, eps), 1);
              try
                  set(h(2),'SliderStep',[stp stp]);
              catch
              end
          end
      end
      % Record Length slider: 3 fixed levels (2048/4096/8192).
      if ischar(In4) && ~isempty(strfind(lower(In4),'set_frmszro'))
          try
              set(h(2),'SliderStep',[0.5 0.5]);
          catch
          end
      end
      
      h(3) =  uicontrol(hf,'Style','text','visible',In6,...
                        'String',min_str,...
                        'BackGroundColor',In8(1,:),...
                        'ForeGroundColor',In8(3,:),...
                        'userdata',fmt,...
                        'HorizontalAlignment','left');

      h(4) =  uicontrol(hf,'Style','text','visible',In6,...
                        'String',max_str,...
                        'BackGroundColor',In8(1,:),...
                        'ForeGroundColor',In8(3,:),...
                        'userdata',In4,...
                        'HorizontalAlignment','right');

      h(5) =   uicontrol(hf,'Style','edit','visible',In6,...
                        'String',ftoa(fmt(2,:),In2(3)),...
                        'BackgroundColor',In8(2,:),... 
                        'ForeGroundColor',In8(4,:),...
                        'userdata',In2(4:5),...
                        'HorizontalAlignment','center',...
                        'Callback',sprintf('islider(%d,''CBedit'')',Out1));
      
      % store mode and optional quantization value
      h(6)=In7(1);
      if length(In7) >=2 
          h(7)=In7(2); 
      end
      set(h(1),'userdata',h);
      islider(Out1,'set','position',In1);
                        
   elseif strcmp(Action,'get')
       Out1 = s2n(get(h(5),'String'));  % v5, this could cause some problems
       % Out1 = get(h(slide),'Value');

   elseif strcmp(Action,'CBedit')       % call back for edit box
          % set slider position
          newval = s2n(get(h(5),'String')); 
          if isempty(newval) 
              set(h(5),'String',ftoa(fmt(2,:),get(h(2),'value'))); 
          else
              if h(6)==1 || h(6)==5  % no tweaking needed
              elseif h(6)==2     % nearest integer
                 newval= round(newval);
              elseif h(6)==3     % limit to 2^N
                 cb = get(h(4),'userdata');
                 if is_record_length_slider(cb)
                    newval = quantize_record_length(newval, get(h(2),'min'), get(h(2),'max'));
                 else
                    newval = 2 ^ nextpow2(newval/1.414);
                 end
              elseif h(6)==4    % quantize to value in h(quant)
                 newval=h(7)*round(newval/h(7));  
              else
                  disp([num2str(h(6)),': unsupported mode in islider.mi']);
              end;
              
              minmax=get(h(5),'userdata');
              newval=min(newval,minmax(2));
              newval=max(newval,minmax(1));
              set(h(5),'String',ftoa(fmt(2,:),newval));
              newval = max(min(get(h(2),'max'),newval),get(h(2),'min'));  % v5
              set(h(2),'Value',newval,'userdata',newval);
              eval(get(h(4),'userdata'));  % invoke user callback function
          end;

   elseif strcmp(Action,'CBslide')       % call back for slider
          % sets value readout
             newval = get(h(2),'Value');
             % userdata has last value
             if h(6)==1 
                  % no tweak needed
             elseif h(6)==2
                  newval= round(newval);  
                  % this simple operation does not work well for 
                  % abs(min-max) < about 50
                  % the arrows won't move the slider
                                          
              elseif h(6)==3
                  cb = get(h(4),'userdata');
                  if is_record_length_slider(cb)
                      newval = quantize_record_length(newval, get(h(2),'min'), get(h(2),'max'));
                      set(h(5),'String',ftoa(fmt(2,:),newval));
                      set(h(2),'Value',newval,'userdata',newval);
                      eval(get(h(4),'userdata'));
                      return;
                  end
                  % power-of-two mode:
                  % - large jump (track click): move one tick (2^N) by direction
                  % - small move (drag/arrow): quantize to nearest 2^N
                  smin = get(h(2),'min');
                  smax = get(h(2),'max');
                  oldval = get(h(2),'userdata');
                  if oldval <= 0
                      oldval = smin;
                  end
                  oldpow = 2^nextpow2(oldval);
                  if oldpow > oldval
                      oldpow = oldpow/2;
                  end
                  if oldpow < smin
                      oldpow = smin;
                  end
                  if oldpow > smax
                      oldpow = smax;
                  end

                  delta = newval - oldval;
                  big_jump = abs(delta) > max((smax-smin)/8, max(oldpow,1)*0.3);
                  if big_jump
                      if delta > 0
                          newval = min(oldpow*2, smax);
                      elseif delta < 0
                          newval = max(oldpow/2, smin);
                      else
                          newval = oldpow;
                      end
                  else
                      if newval <= 0
                          newval = smin;
                      else
                          newval = 2^round(log(newval)/log(2));
                      end
                  end
                  newval = min(max(newval,smin),smax);
                  
              elseif h(6)==4
                  % quantize to value in h(quant)
                  newval=h(7)*round(newval/h(7));

              elseif h(6)==5
                  cb = get(h(4),'userdata');
                  if force_linear_mode5(cb)
                      % Compatibility override for specific controls:
                      % Input Offset / Stop at Count / Delay.
                      if ~isempty(strfind(cb,'set_tcntro'))
                          q = 5;  % Stop at Count step
                      elseif length(h) >= 7 && ~isempty(h(7))
                          q = h(7);
                      else
                          q = 0;
                      end
                      if q > 0
                          newval = q*round(newval/q);
                      end
                      newval = min(max(newval, get(h(2),'min')), get(h(2),'max'));
                  else
                      % quasi log steps
                      % see what slider mode was pushed with sldclick
                      click_type = sldclick(h(2));
                      if (click_type ~= 0) && (click_type ~= 20)  
                          oldval     = get(h(2),'userdata');
                          % h(quant) has minimum step size
                          if abs(oldval)>0 
                             offset = -1.5;
                             step  = click_type*max(10^( round(offset+log10(abs(oldval)))),h(7));
                          else
                             step  = click_type*h(7); 
                          end;
                          % make newval limited multiple of the step size....
                          newval = min(max(step*round((oldval+step)/step), get(h(2),'min')),get(h(2),'max'));
                      end;
                  end
              else
                  disp([num2str(h(6)),': unsupported mode in islider.mi']);
              end;
              set(h(5),'String',ftoa(fmt(2,:),newval));
              set(h(2),'Value',newval,'userdata',newval);
              eval(get(h(4),'userdata'));  % invoke user callback function
       
   elseif strcmp(Action,'set')
       if strcmp(In1,'value')
           % complete overhaul for v5 
           eminmax = get(h(5),'userdata');       % get edit minmax range
           set(h(5), 'string',ftoa(fmt(2,:),max(min(In2,eminmax(2)),eminmax(1))));   % set limited value
           Out1    = s2n(get(h(5),'String'));    % return limited, quantized value
           newval  = max(min(get(h(2),'max'),Out1),get(h(2),'min'));
           set(h(2),'value',newval,'userdata',newval); % keep slider history coherent for QUASILOG mode
      
       elseif strcmp(In1,'newquant')
           h(7) = In2;
           newval = h(7)*round(get(h(2),'Value')/h(7));
           Out1   = newval;
           set(h(5),'String',ftoa(fmt(2,:),newval));
           set(h(2),'Value',newval,'userdata',newval);
           set(h(1),'userdata',h); % save updated quantization    

       elseif strcmp(In1,'minmax')   
           set(h(5),'userdata',In2(3:4));
           set(h(3),'string',ftoa(fmt(1,:),In2(1)));
           set(h(4),'string',ftoa(fmt(3,:),In2(2)));
           oldval   = s2n(get(h(5),'String'));  % v5, this could cause some problems
           if nargin >=7
              % possibly an optional target value has been supplied
              if ~isempty(In5)
                 oldval = In5;    % v5 , set oldval to it and proceed
              end  
           end
           
           % limit slider value to new min/max (edit limits)
           newval=max(min(oldval,In2(4)),In2(3));   % limit to edit box min-max values
           if newval ~=oldval
              set(h(5),'string',ftoa(fmt(2,:),newval));
           end;
           newval = max(min(In2(2),newval),In2(1));
           set(h(2),'min',In2(1),'max',In2(2),'value',newval,'userdata',newval); % v5
           Out1=s2n(get(h(5),'String'));        % v5 return limited quantized value
           
           if nargin >=5
             if ~isempty(In3)
                 set(h(1),'string',In3);
             end;    
           end;
           
           if nargin >=6
             if ~isempty(In4)
                set(h(1:5),'visible',In4);
             end   
           end
           
       elseif strcmp(In1,'label')
           set(h(1),'string',In2);
           if nargin >=5
             set(h(1:5),'visible',In3)
           end
        
       elseif strcmp(In1,'vis_on')
           if nargin >3
              if strcmp(In2,'no_label') 
                 % no label
                 set(h(1),'visible','off');
                 set(h(2:5),'visible','on')
              else
                 disp('use no_label arg in islider.m');
              end;
           else
              if strcmp('',get(h(1),'string'))
                 % no label string, leave vis off
                 set(h(1),'visible','off');
                 set(h(2:5),'visible','on')
              else
                 set(h(1:5),'visible','on')
              end;   
           end;
       elseif strcmp(In1,'vis_off') 
           set(h(1:5),'visible','off');
           
       elseif strcmp(In1,'ena_on')     
            set(h(2),'enable','on');
            set(h(5),'enable','on');
       elseif strcmp(In1,'ena_off')
            set(h(2),'enable','off');
            set(h(5),'enable','off');

       elseif strcmp(In1,'position')
         lmin = 7*fix(s2n(fmt(1,2:(length(deblank(fmt(1,:)))-1)))) + 7;
         lmax = 7*fix(s2n(fmt(3,2:(length(deblank(fmt(3,:)))-1)))) + 7;
         lval = In2(3)-(lmin+lmax);
        a = (get(0,'screenpix')-96)/10;  % Adjustment for large fonts
        y1 = In2(2) - 17 - a;  h1 = 17+a;
        set(h(1), 'Position',[In2(1:3) 16+a/2]);
        set(h(2), 'Position',[In2(1),In2(2)-33,In2(3),17-1-a]);
        set(h(3),'Position',[In2(1),y1,lmin,h1]);
        set(h(4),'Position',[In2(1)+In2(3)-lmax,y1,lmax,h1]);
        set(h(5), 'Position',[In2(1)+lmin,y1-1,lval,h1+2]); 
       end;
   else
      disp([Action,': Action unrecognized in islider.m'])
   end;  % large if
% end function islider

function tf = force_linear_mode5(cb)
tf = false;
if isempty(cb)
    return;
end
if iscell(cb)
    try
        cb = cb{1};
    catch
        cb = '';
    end
end
if isstring(cb)
    cb = char(cb);
end
if ~ischar(cb)
    try
        cb = char(cb);
    catch
        cb = '';
    end
end
cb = lower(cb);
tf = ~isempty(strfind(cb,'set_ofsro')) || ...
     ~isempty(strfind(cb,'set_tcntro')) || ...
     ~isempty(strfind(cb,'set_delayro'));

function tf = is_record_length_slider(cb)
if isempty(cb)
    tf = false;
    return;
end
if iscell(cb)
    try
        cb = cb{1};
    catch
        cb = '';
    end
end
if isstring(cb)
    cb = char(cb);
end
if ~ischar(cb)
    try
        cb = char(cb);
    catch
        cb = '';
    end
end
cb = lower(cb);
tf = ~isempty(strfind(cb,'set_frmszro'));

function v = quantize_record_length(v, smin, smax)
allowed = [2048 4096 8192];
allowed = allowed(allowed >= smin & allowed <= smax);
if isempty(allowed)
    v = min(max(v,smin),smax);
    return;
end
[~,ix] = min(abs(allowed - v));
v = allowed(ix);
