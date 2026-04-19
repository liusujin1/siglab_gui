  function out1 = progbar(Action,In1,In2)
% function out1 = progbar(Action,In1,In2)
% progress bar object to let user know how close they may be to Nirvana 
% Action
%      'init'
%           create object
%           size and position in In1.pos
%           color in             In1.color 
%           title in             In1.title
%           'normal' or 'modal'  In1.style
%      'update'
%           percent complete in In1
%      'close'
%
% Dick Benson, DSPT 

switch  Action
     case 'init' 
          hndls.figure = figure('position',In1.pos,...
                                'menu','none',...
                                'Name',In1.title,...
                                'NumberTitle','off','visible','on',...
                                'BackingStore','off',...
                                'windowstyle',In1.style,...
                                'CloseRequestFcn','');
          pfh = hndls.figure;
          
          hndls.axes  = axes('Parent',pfh,'Units','pixels','Position',In1.pos+[-90,-25,-20,-30],...
                                  'Box','on',...
                                  'NextPlot','add',...
                                  'DrawMode','fast',...
                                  'Color',[0 0 0],...
                                  'YTickLabelMode','manual',...
                                  'xlim',[0,100],...
                                  'ylim',[0 1],...
                                  'TickDir','in');
          hndls.patch = patch('Parent',hndls.axes,...
                              'EdgeColor',In1.color,...
                              'EraseMode','background',...
                              'FaceColor',In1.color);
                      
          set(pfh,'userdata',hndls);            
          out1 = pfh;
         % set(hndls.patch,'xdata', [0, 0, 50 50],...
         %                 'ydata', [0, 1, 1  0]);
     case 'update'
          hndls = get(In1,'userdata');
          set(hndls.patch,'xdata', [0, 0, In2 In2],...
                          'ydata', [0, 1, 1  0]);
          drawnow;
     case 'close'
          hndls = get(In1,'userdata');
          set(hndls.figure,'CloseRequestFcn','closereq');
          close(hndls.figure);

end;  % switch and function 
    










