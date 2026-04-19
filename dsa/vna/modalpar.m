   function Out1 = modalpar(hin,Action,In1,In2)
%  function Out1 = modalpar(hin,Action,In1,In2) 
% Action
%    'init'
%       hin = handle of owner
%       In1 has callback (string) to owner when apply button is pressed
%       In2 has the initial state 
%       Out1  returns handle of this modal figure
%
%    'apply' executes callback loaded by 'init' action. 
%    'get&close'
%       Out1 has structure of states,labels (like In2), dialog closes
%       
%    'cancel' closes this dialog with no other action
%
%    Dick Benson, DSPT

   switch Action
      case 'init'
         % handle of owner to extract some properties
         pos_owner = get(hin,'position');
         wp   = 70;  % size & spacing of objects
         sx   = 10;
         sy   = 6;
         hp   = 14;
         hpb  = 20;
         wpb  = 70;
         wto  = 180;
         weo  = 45;
         x0   = 10;

         wf   = 270; % width of figure
         x1   = 75;
         x2   = 150;
         hf   = 120; 
      
         hfig=figure('position',pos_clip([pos_owner(1:2)+[pos_owner(3)-wf-sx,pos_owner(4)-hf-sy],wf,hf],[wf,hf]),... 
                     'menu','none',...
                     'Name','Modal Parameters',...
                     'NumberTitle','off',...
                     'WindowStyle','modal');

         Out1 = hfig; 
         
         uicontrol(hfig,'Style','pushbutton','visible','on',...
                        'FontName','ms sans serif',...
                        'FontSize',8,...
                        'FontWeight','bold',...
                        'HorizontalAlignment','left',...
                        'string','Apply',...
                        'callback','modalpar([],''apply'')',...
                        'Position',[x1,sy,wpb,hpb]);
                      
         uicontrol(hfig,'Style','pushbutton','visible','on',...
                        'FontName','ms sans serif',...
                        'FontSize',8,...
                        'FontWeight','bold',...
                        'HorizontalAlignment','left',...
                        'string','Cancel',...
                        'callback','modalpar([],''cancel'')',...
                        'Position',[x2,sy,wpb,hpb]);

         pc = char(37); 
         labels = {'double hit amplitude %','double hit delay %','force window size in %','exponential window decay %'};
         cbstr  = {'dblpcnt','dbldelay','forcewin','expdecay'};
         ttstring = {'Used only when double hit reject is selected in the Setup PROCESSING controls ',...
                     'Used only when double hit reject is selected in the Setup PROCESSING controls ',...
                     'Used only when the User Defined Window is selected in the Setup PROCESSING controls',...
                     'Used only when the User Defined Window is selected in the Setup PROCESSING controls'};
         
         value  = {In2.dblpcnt,In2.dbldelay,In2.forcewin,In2.expdecay};
         
         for i = 1:4
              uicontrol(hfig,'Style','text','visible','on',...
                             'FontName','ms sans serif',...
                             'FontSize',8,...
                             'FontWeight','bold',...
                             'HorizontalAlignment','left',...
                             'string',labels{i},...
                             'callback','',...
                             'tooltipstring',ttstring{i},...
                             'Position',[sx,sy+(5.5-i)*hpb,wto,hpb]);
         
            
              hlh.h(i) = uicontrol(hfig,'Style','edit','visible','on',...
                             'FontName','ms sans serif',...
                             'FontSize',8,...
                             'FontWeight','bold',...
                             'HorizontalAlignment','left',...
                             'string',sprintf('%5.1f',value{i}),...
                             'backgroundcolor',[1,1,0],...
                             'callback',['modalpar(','[],','''set'',''',cbstr{i},''')'],...
                             'Position',[2*sx+wto,sy+(5.5-i)*hpb,weo,hpb]);
         end;
         
         
         
         hlh.owner       = hin;    % figure handle of owner 
         hlh.apply_cb    = In1;    % string to be executed when "apply" button is pressed
         hlh.state       = In2;     
         
         set(hfig,'userdata',hlh);
      
      case 'set'
         hlh = get(gcbf,'userdata');
         switch In1
             case 'dblpcnt'
                  i = 1;
                  %                                       max  min
                  n = max(min(s2n(get(hlh.h(i),'string')),100),10);
                  if ~isempty(n)
                     hlh.state.dblpcnt = n;
                  else   
                     n = hlh.state.dblpcnt;
                  end;
             case 'dbldelay'
                  i = 2;
                  %                                       max  min
                  n = max(min(s2n(get(hlh.h(i),'string')),50),20);
                  n = s2n(get(hlh.h(i),'string'));
                  if ~isempty(n) 
                     hlh.state.dbldelay = n;
                  else
                     n = hlh.state.dbldelay;
                  end;
             case 'forcewin'
                  i = 3;
                  %                                       max  min
                  n = max(min(s2n(get(hlh.h(i),'string')),100),5);
                  if ~isempty(n) 
                     hlh.state.forcewin = n;
                  else
                     n = hlh.state.forcewin;  
                  end;
             case 'expdecay'
                  i = 4;
                  %                                       max  min
                  n = max(min(s2n(get(hlh.h(i),'string')),100),1);
                  if ~isempty(n) 
                     hlh.state.expdecay = n;
                  else
                     n= hlh.state.expdecay; 
                  end;
         end;
          
         set(hlh.h(i),'string',sprintf('%5.1f',n));
         set(gcbf,'userdata',hlh);
         
      case 'apply'
         hlh = get(gcbf,'userdata');
         eval(hlh.apply_cb);
         
      case 'get&close' 
         hlh   = get(gcbf,'userdata');
         Out1 = hlh.state;
         close(gcbf)
         
      case('cancel')
         close(gcbf);
         
      otherwise
        disp([Action,' not recognized in modalpar.m']);
   end; % switch Action
% end modalpar function                                    





