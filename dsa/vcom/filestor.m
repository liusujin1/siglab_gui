   function Out1 = filestor(hin,Action,In1,In2)
%  function Out1 = filestor(hin,Action,In1,In2) 
% Action
%    'init'
%       hin = handle of owner
%       In1 has callback (string) to owner when apply button is pressed
%       In2 has the initial state 
%       In2.state   cell array of states
%       In2.label   cell array of labels
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
         wpb  = 60;
         x0   = 10;
         maxr = length(In2.state);   % number of check boxes 
         y0   = (maxr/2)*(hp+sy)+hpb+2*sy;
         wf   = 180; % width of figure
         hf   = (maxr+1)*(hp+sy)/2+2*hp+hpb;                  % height of figure
      
         
         
         
         
         
         hfig=figure('position',pos_clip([pos_owner(1:2)+[pos_owner(3)-wf-sx,pos_owner(4)-hf-sy],wf,hf],[wf,hf]),... 
                     'menu','none',...
                     'Name','File Storage',...
                     'NumberTitle','off',...
                     'WindowStyle','modal');

         Out1 = hfig; 
         
         uicontrol(hfig,'Style','pushbutton','visible','on',...
                        'FontName','ms sans serif',...
                        'FontSize',8,...
                        'FontWeight','bold',...
                        'HorizontalAlignment','left',...
                        'string','Apply',...
                        'callback','filestor([],''apply'')',...
                        'Position',[x0,sy,wpb,hpb]);
                      
         uicontrol(hfig,'Style','pushbutton','visible','on',...
                        'FontName','ms sans serif',...
                        'FontSize',8,...
                        'FontWeight','bold',...
                        'HorizontalAlignment','left',...
                        'string','Cancel',...
                        'callback','filestor([],''cancel'')',...
                        'Position',[wp+3*sx,sy,wpb,hpb]);
        
         bcolor = [1 1 0];             
         row = 1;             
         while row <= maxr
              yp = y0-fix((row-1)/2)*(hp+sy);
              hlh.hckb(row) = uicontrol(hfig,'Style','checkbox','visible','on',...
                                             'BackGroundColor',bcolor,...
                                             'Position',[sx+rem(row-1,2)*(wp+2*sx),yp,wp,hp],...
                                             'string',In2.label{row},...
                                             'value',In2.state{row});
             row = row+1;
         end;
         
         hlh.owner       = hin;    % figure handle of owner 
         hlh.apply_cb    = In1;    % string to be executed when "apply" button is pressed
         hlh.maxr        = maxr;
         hlh.label       = In2.label;
         set(hfig,'userdata',hlh);
      
      case 'apply'
         hlh = get(gcbf,'userdata');
         eval(hlh.apply_cb);
         
      case 'get&close' 
         hlh        = get(gcbf,'userdata');
         Out1.label = hlh.label;
         Out1.state = get(hlh.hckb,'value');  % returns cell array of states
         s = 0;
         for i=1:length(Out1.state)
            s=s+Out1.state{i};
         end;
         if s==0
           msgbox(['You have not selected any functions to be stored in the file when/if you save it.',...
                   'You may want to reconsider this.'],...
                   'SigLab Warning',...
                   'warn',...
                   'modal');
         end;
         close(gcbf)
         
      case('cancel')
         close(gcbf);
         
      otherwise
        disp([Action,' not recognized in filestor.m']);
   end; % switch Action
% end filestor function                                    

