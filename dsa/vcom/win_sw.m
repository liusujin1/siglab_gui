  function win_sw(Action,In1)
% function win_sw(Action,In1)
% Window switching pseudo object menu scheme
% Replaces MATLAB windows switching menu. 
% Call after figure window creation to provide window switching capability
% Normally done in an init section of code. 
% e.g.
%      h= figure       % parent figure creation
%      uimenu()        % other menus
%      win_sw('init')  % window switch menu
%      uimenu()        % subsequent menus   
% Objectified version of PM's inline code
% must be v4.2c or later (!!) to work since callback from main
% (top level) menu only works in 4.2c and beyond (I assume)  
% Actions
%         'init'      initialize this pseudo object
%         'load'      callback for main menu, not reliable Arghhhh
%         'switch'        callback for submenus
%             1,2,3,4,5   In1 has which submenu made callback  
% Caveats: Main menu ('&Windows') callback seems to work properly if you have first 
%          touched something in the figure window. 
%          Touching menu items 1st does not always work.
%          Tweaked for MATLAB 5, no support for < 5
%          Cannot get back to command window using MATLAB 5 
% Dick Benson, DSP Technology
   
   if ~strcmp(Action,'init')
       % should only have 1 menu named 'Win' per figure.
       % uncomment line below for debug of 'load' callback
       % get(gcf,'name')
       hsw_      = get(findobj(gcbf,'type','uimenu','Label','&Win'),'userdata');
       kid_figs  = get(0,'children');    % get children figure windows of MATLAB command window
   end; 
   % --------------------------  init ---------------------------------------
   if strcmp(Action,'init')
       % create menus in parent figure
       % top menu object
       if nargin ==2
          hf = In1;
       else
          hf = gcf;
       end;
       hsw_(1) = uimenu(hf,'Label','&Win','Callback','win_sw(''build'');');
       for k=2:6
           % submenus
           if k==2 s ='MATLAB Command Window'; else s=''; end; 
           hsw_(k)=uimenu(hsw_(1),'Label',s,...
                               'Callback',['win_sw(''switch'',',int2str(k-1),');']);
       end;
       set(hsw_(1),'userdata',hsw_);  % stash set of handles in main menu object
   % --------------------------  build  ---------------------------------------
   elseif strcmp(Action,'build')
       % build menu list of max 5 other figure windows
       for k = 2:5                                  % don't need to switch to owner, skip 1st entry
           if k > length(kid_figs)  
                 set(hsw_(k+1),'visible','off');    % hide excess submenus
           else
                 figname = get(kid_figs(k),'name');                  % get figure name of a kid
                 if isempty(figname) figname = ['Figure No. ',int2str(kid_figs(k))]; end;
                 set(hsw_(k+1),'visible','on','label',figname);      % show submenu (up to five)
           end;
       end;  
   % -------------------------- switch  ---------------------------------------
   elseif strcmp(Action,'switch')
       if beyondv4
          if In1==1
             % figure(0); % it worked in a previous version of v5 .. but no more ... 
               tmsg('Cannot get to MATLAB window in V5 with menu. Use alt-tab or task bar, ... sorry.',4,'','');
          else
             figure(kid_figs(In1));
          end;
       else
          if In1==1 shc; else figure(kid_figs(In1)); end;  % switch to it (cmnd window 1 is special)
          showfig(get(hsw_(In1+1),'label'));               % in case selected figure is minimized
       end;
   else 
       disp([Action,' not recognized in win_sw.m']);
   end;
%end win_sw function













