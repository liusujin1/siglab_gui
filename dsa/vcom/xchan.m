   function [Out1, Out2] = xchan(hin,Action,In1,In2)
%  function [Out1, Out2] = xchan(hin,Action,In1,In2) 
% Action
%    'init'
%       hin = handle of owner
%       In1 has callback (string) to owner when apply button is pressed
%       In2 has the initial state of the check boxes
%       In3 has number of channels in current system
%       In4 has number of channels in first box (limits number of ref channels)
%       Out1  returns handle of the figure used to hold the check boxes
%    'apply' executes callback loaded by 'init' action. 
%    'check' callback when check box is selected (internal)
%    'get_state'
%       In1 'close' (optional) will close this modal dialog
%       Out1 has structure of states
%       Out2 has raw check box states plus info passed on 'init' action 
%    'cancel' closes this dialog with no other action
%
%    Dick Benson, DSPT

   switch Action
      case 'init'
         % handle of owner to extract some properties
         pos_owner = get(hin,'position');
        
         xc_rmax = In2.xc_rmax;  % number of rows = number of channels in one box
         xc_cmax = In2.xc_cmax;  % number of columns = number of total channels
         
         wp   = 14;  % size & spacing of objects
         sx   = 10;
         sy   = 6;
         hp   = 14;
         hpb  = 20;
         wpb  = 60;
         
         x0   = 35;
         y0   = (xc_rmax+1)*(hp+sy)+hpb+2*sy;
         wf = max(xc_cmax*(wp+sx)+2*(x0+wp), 2*wpb+2*x0+sx); % width of figure
         hf = (xc_rmax+1)*(hp+sy)+2*hp+hpb;                  % height of figure
         
         hfig=figure('position',[pos_owner(1:2)+[pos_owner(3)-wf-sx,pos_owner(4)-hf-sy],wf,hf],... 
                     'menu','none',...
                     'Name','Cross Channel Calculation Matrix',...
                     'NumberTitle','off',...
                     'WindowStyle','modal');

         Out1 = hfig; 
         
         applypb = uicontrol(hfig,'Style','pushbutton','visible','on',...
                        'FontName','ms sans serif',...
                        'FontSize',8,...
                        'FontWeight','bold',...
                        'HorizontalAlignment','left',...
                        'string','Apply',...
                        'enable','off',...
                        'callback','xchan([],''apply'')',...
                        'Position',[x0+2*sx,sy,wpb,hpb]);
                      
         uicontrol(hfig,'Style','pushbutton','visible','on',...
                        'FontName','ms sans serif',...
                        'FontSize',8,...
                        'FontWeight','bold',...
                        'HorizontalAlignment','left',...
                        'string','Cancel',...
                        'callback','xchan([],''cancel'')',...
                        'Position',[x0+3*sx+wpb,sy,wpb,hpb]);
                      
         for row=1:xc_rmax
              yp = y0-row*(hp+sy);
              uicontrol(hfig,'Style','text','visible','on',...
                             'FontName','fixedsys',...
                             'FontSize',11,...
                             'FontWeight','normal',...
                             'HorizontalAlignment','left',...
                             'string',sprintf('ref:%1d',row),...
                             'Position',[sx,yp,x0+sx,hp]);
           
           
            vis = 'on';   
            for col = 1:xc_cmax
                xp = x0+(col)*(wp+sx);
                if col == row
                   bcolor = [1 0 0];
                else
                   bcolor = 0.5*[1 1 1];
                end;
                if row ==1
                    uicontrol(hfig,'Style','text','visible','on',...
                                   'FontName','fixedsys',...
                                   'FontSize',11,...
                                   'FontWeight','normal',...
                                   'HorizontalAlignment','center',...
                                   'string',sprintf('%1d',col),...
                                   'Position',[xp-5,yp+hp+4,wp+8,hp]);
                end;
                
                %if col >= row 
                %   vis='on';
                %else
                %   vis='off';   % don't show these till/when/if ever ...  SigLab SW supports it
                %end;
               
                     hlh.hckb(row,col) = uicontrol(hfig,'Style','checkbox','visible','on',...
                                                        'BackGroundColor',bcolor,...
                                                        'Position',[xp,yp,wp,hp],...
                                                        'value',In2.xc_ckstate(row,col),...
                                                        'visible',vis,...
                                                        'callback',['xchan([],''check'',',int2str(row),',',int2str(col),');']);
            end;
         end;
         
         set(hlh.hckb(1,1),'enable','off','value',1);   % force channel 1 to be on all the time, as a reference.
       
         hlh.owner       = hin;    % figure handle of owner 
         hlh.apply_cb    = In1;    % string to be executed when "apply" button is pressed
         hlh.xc_ckstate  = In2.xc_ckstate;    
         hlh.xc_rmax     = xc_rmax;
         hlh.xc_cmax     = xc_cmax;
         hlh.applypb     = applypb;
         
         set(hfig,'userdata',hlh);
      
      case 'apply'
         hlh = get(gcbf,'userdata');
         eval(hlh.apply_cb);
         
         
      case 'check'
         hcb   = gcbf;
         hlh   = get(hcb,'userdata');
         
         set(hlh.applypb,'enable','on');
         
         row   = In1;    % which check box changed state
         col   = In2;    % ditto 
         hlh.xc_ckstate(row,col) = get(hlh.hckb(row,col),'value'); % get state of check box
         
         % First, there must be at least one channel on, so if user 
         % trys to turn them all off, force channel 1 to go on.
         % In fact, just leave channel 1 on all the time. 
        
         % second, if a channel is turned on in a column, turn on the associated ref channel
         % since this will be the usual case. This can, however, be overridden by user.
         if get(hlh.hckb(row,col),'value') ==1
            set(hlh.hckb(row,row),'value',1);
            hlh.xc_ckstate(row,row)   = 1;
         end;
         set(hcb,'userdata',hlh);
      
      case { 'get_state' , 'translate'}
         switch  Action
            case 'get_state'
                 hlh = get(hin,'userdata');  % hin has this figure's handle
            case 'translate'
                 hlh = hin; % pass structure with checkbox states directly in hin (not a handle in this context)
         end;
         c.r = [];
         resp(1:hlh.xc_rmax)=c;
         clear c
         Out1.resp   = resp;
         Out1.refc   = [];
         Out1.clist  = [];

         for i=1:hlh.xc_rmax
             % first, pick out "ref channels" and associated response channels
             if hlh.xc_ckstate(i,i) == 1
                % check to see if any other channel is on in this row
                hlh.xc_ckstate(i,i)=0; % temp
                if sum(hlh.xc_ckstate(i,1:hlh.xc_cmax)) >0
                   Out1.refc=[Out1.refc,i];
                end;
                % then pick out "response" channels for each reference
                Out1.resp(i).r = find(hlh.xc_ckstate(i,1:hlh.xc_cmax));
                hlh.xc_ckstate(i,i) = 1;   % restore
             end;
             % then, create the equivalent of clist, append any channel that is "on"
             Out1.clist = union(Out1.clist,find(hlh.xc_ckstate(i,1:hlh.xc_cmax)));
             Out1.clist = Out1.clist(:)';
         end;
         Out2 = hlh;
         if nargin ==3 & strcmp(Action,'get_state') & strcmp(In1,'close')
            close(hin)
         end;   
         
      case('cancel')
         close(gcbf);
         
      otherwise
        disp([Action,' not recognized in xchan.m']);
   end; % switch Action
% end xchan function                                    

